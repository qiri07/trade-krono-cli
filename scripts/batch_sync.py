#!/usr/bin/env python3
"""
批次轮转增量同步脚本（v3 — 多 Provider 均衡派发版）。

策略：
  1. 从待拉取股票列表每次取 BATCH_SIZE=100 只，按 ticker 排序确保确定性
  2. 健康检查所有 Provider（baostock 因全局会话非线程安全，独立串行处理）
  3. 每个 Provider 维护独立的 worker 线程，从共享队列取任务串行执行
     （串行避免 mootdx/baostock 的限流和线程安全问题）
  4. round-robin 将本批股票分配到各 Provider 队列
  5. 本批所有任务完成后休息 BETWEEN_BATCH_DELAY，再取下一批
  6. 定期重新检测健康，支持失败自动降级（连续 N 次失败禁用）和恢复

并发模型：
  主线程   → 分配任务到各 Provider 队列（round-robin）
  Provider 线程 → 串行拉取分配给自己的股票
  等待所有 Provider 线程完成后进入下一批

Per-Provider 串行，但多 Provider 之间并行执行。
"""
from __future__ import annotations

import queue
import sqlite3
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dataclasses import dataclass

from loguru import logger

from trade_krono_cli.cli_commands.core import _load_env
from trade_krono_cli.data import fetch_kline_incremental
from trade_krono_cli.data_providers.factory import get_data_factory

# ─── 参数配置 ─────────────────────────────────────────────────────────────────
END_DATE = "2026-09-09"
BATCH_SIZE = 100

# 每只股票与同 Provider 下一只之间的最小间隔（秒）
# 串行模式下这是唯一限速机制
PROVIDER_INTERVAL: dict[str, float] = {
    "mootdx": 0.8,
    "tonghuashun": 0.8,
    "baostock": 1.0,
}

# 批次间冷却（秒）
BETWEEN_BATCH_DELAY = 5.0
# 单只股票超时（秒）
TIMEOUT_SEC = 60.0
# 每隔多少批次重新做一轮健康检查
HEALTH_CHECK_INTERVAL = 10
# Provider 连续失败多少次后暂时移除
FAILURE_THRESHOLD = 5
# Provider 连续成功多少次后恢复
RECOVERY_THRESHOLD = 3
# ─────────────────────────────────────────────────────────────────────────────

_load_env()

CACHE_DB = Path("/run/media/onai/MyDisk/Work/trade-krono-cli/outputs/cache/pipeline_cache.db")


def get_stale_tickers(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    """获取所有 end < END_DATE 的股票 (ticker, start_date)，按 MAX(end) 去重。"""
    cur = conn.cursor()
    cur.execute('''
        SELECT ticker, MIN(start) AS start
        FROM kline_cache
        GROUP BY ticker
        HAVING MAX(end) < ?
        ORDER BY ticker
    ''', (END_DATE,))
    return cur.fetchall()


class _HealthTracker:
    """Provider 健康状态跟踪器，带滞回机制防止频繁切换。"""

    def __init__(self) -> None:
        self._failures: dict[str, int] = {}
        self._successes: dict[str, int] = {}
        self._disabled: set[str] = set()
        self._lock = threading.Lock()

    def record_success(self, name: str) -> None:
        with self._lock:
            self._failures[name] = 0
            self._successes[name] = self._successes.get(name, 0) + 1
            if name in self._disabled and self._successes[name] >= RECOVERY_THRESHOLD:
                self._disabled.discard(name)
                logger.info(f"  ✅ Provider {name} 恢复可用")

    def record_failure(self, name: str) -> None:
        with self._lock:
            self._successes[name] = 0
            self._failures[name] = self._failures.get(name, 0) + 1
            if (
                self._failures[name] >= FAILURE_THRESHOLD
                and name not in self._disabled
            ):
                self._disabled.add(name)
                logger.warning(
                    f"  ⛔ Provider {name} 连续失败 {FAILURE_THRESHOLD} 次，暂时禁用"
                )

    def is_available(self, name: str) -> bool:
        with self._lock:
            return name not in self._disabled

    def get_available(self, candidates: list[str]) -> list[str]:
        with self._lock:
            return [n for n in candidates if n not in self._disabled]

    def reset(self) -> None:
        with self._lock:
            self._failures.clear()
            self._successes.clear()
            self._disabled.clear()


_health = _HealthTracker()


def check_provider_health() -> dict[str, bool]:
    """并发检测各 Provider 健康状态。"""
    factory = get_data_factory()
    result: dict[str, bool] = {}
    for name in PROVIDER_INTERVAL:
        try:
            p = factory.get_provider(name)
            result[name] = (p is not None and p.health_check()) if p else False
        except Exception:
            result[name] = False
    return result


# ─── Provider Worker ──────────────────────────────────────────────────────────


@dataclass
class _WorkerResult:
    ticker: str
    ok: bool


class _ProviderWorker(threading.Thread):
    """单个 Provider 的串行工作线程。

    从共享队列取任务，串行执行，通过 interval 控制请求频率。
    """

    def __init__(
        self,
        name: str,
        task_queue: queue.Queue[tuple[str, str]],
        result_queue: queue.Queue[_WorkerResult],
        interval: float,
    ) -> None:
        super().__init__(daemon=True, name=f"worker-{name}")
        self._name = name
        self._queue = task_queue
        self._result_queue = result_queue
        self._interval = interval
        self._last_done: float = 0.0
        self._lock = threading.Lock()
        self.start()

    def _rate_limit(self) -> None:
        """确保两次请求之间至少间隔 _interval 秒。"""
        with self._lock:
            now = time.time()
            elapsed = now - self._last_done
            if elapsed < self._interval:
                time.sleep(self._interval - elapsed)
            self._last_done = time.time()

    def run(self) -> None:
        logger.info(f"  🟢 [{self._name}] worker 启动，interval={self._interval}s")
        processed = 0
        while True:
            try:
                item = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue
            if item is None:  # 终止信号
                logger.info(f"  ⏹️  [{self._name}] worker 收到终止信号，退出 (共处理 {processed} 只)")
                break

            ticker, start = item
            self._rate_limit()
            try:
                df = fetch_kline_incremental(
                    ticker, start, END_DATE, frequency="d", adjustflag="1", use_cache=True
                )
                ok = df is not None and len(df) > 0
                processed += 1
                if processed % 10 == 0 or processed == 1:
                    logger.info(f"  [{self._name}] 进度: {processed}/?, 最近={ticker} {'✅' if ok else '❌'}")
                self._result_queue.put(_WorkerResult(ticker=ticker, ok=ok))  # type: ignore[misc]
                if ok:
                    _health.record_success(self._name)
                else:
                    _health.record_failure(self._name)
            except Exception as e:
                logger.debug(f"  [{self._name}] {ticker} 异常: {e}")
                self._result_queue.put(_WorkerResult(ticker=ticker, ok=False))  # type: ignore[misc]
                _health.record_failure(self._name)


# ─── Batch Processor ──────────────────────────────────────────────────────────


def process_batch(
    batch: list[tuple[str, str]],
    available_providers: list[str],
) -> tuple[int, int, dict[str, int]]:
    """处理一批股票。

    流程：
      1. 创建各 Provider 的 task_queue + result_queue + worker 线程
      2. round-robin 将股票分配到各队列
      3. 向各队列发送终止信号
      4. 收集所有结果
    """
    if not available_providers:
        logger.warning("  ⚠️  无可用 Provider，跳过本批次")
        return 0, len(batch), {}

    logger.info(f"  📡 可用 Provider: {available_providers}")

    # 初始化各 Provider 的队列和 worker
    task_queues: dict[str, queue.Queue] = {
        name: queue.Queue() for name in available_providers
    }
    result_queues: dict[str, queue.Queue] = {
        name: queue.Queue() for name in available_providers
    }
    workers: dict[str, _ProviderWorker] = {}
    for name in available_providers:
        workers[name] = _ProviderWorker(
            name=name,
            task_queue=task_queues[name],
            result_queue=result_queues[name],
            interval=PROVIDER_INTERVAL.get(name, 1.0),
        )

    # round-robin 分配
    for i, (ticker, start) in enumerate(batch):
        provider = available_providers[i % len(available_providers)]
        task_queues[provider].put((ticker, start))

    # 发送终止信号
    for name in available_providers:
        task_queues[name].put(None)

    # 先等待所有 worker 线程完成，再收集结果
    max_per_worker = max(
        sum(1 for _ in batch if available_providers[i % len(available_providers)] == name)
        for name in available_providers
    )
    join_timeout = max_per_worker * max(PROVIDER_INTERVAL.values()) + 30.0
    logger.info(f"  ⏳ 等待 {len(workers)} 个 worker 完成 (timeout={join_timeout:.0f}s)...")
    for w in workers.values():
        w.join(timeout=join_timeout)

    # 收集所有结果
    success_count = 0
    fail_count = 0
    provider_stats: dict[str, int] = {name: 0 for name in available_providers}
    t_start = time.time()

    for name in available_providers:
        while not result_queues[name].empty():
            result = result_queues[name].get()
            if result.ok:
                success_count += 1
                provider_stats[name] += 1
            else:
                fail_count += 1

    elapsed = time.time() - t_start
    stats_str = "  ".join(
        f"{k}={v}" for k, v in provider_stats.items() if v > 0
    )
    logger.info(
        f"  ✅ 批次完成: 成功={success_count} 失败={fail_count} "
        f"耗时={elapsed:.1f}s  ({stats_str})"
    )

    return success_count, fail_count, provider_stats


# ─── Main ─────────────────────────────────────────────────────────────────────


def main() -> int:
    logger.info(
        f"🚀 批次轮转同步 v3  target={END_DATE}  batch={BATCH_SIZE}  "
        f"providers={PROVIDER_INTERVAL}  "
        f"cooldown={BETWEEN_BATCH_DELAY}s"
    )

    conn = sqlite3.connect(str(CACHE_DB))

    all_stale = get_stale_tickers(conn)
    total = len(all_stale)
    logger.info(f"📋 共 {total} 只股票需补拉")

    if total == 0:
        logger.info("✅ 已是最新，无需同步")
        conn.close()
        return 0

    total_success = 0
    total_fail = 0
    batch_num = 0
    t0 = time.time()
    last_health_check_batch = 0

    for offset in range(0, total, BATCH_SIZE):
        batch_num += 1
        batch = all_stale[offset : offset + BATCH_SIZE]
        elapsed_total = time.time() - t0

        # 定期重新健康检查
        if batch_num - last_health_check_batch >= HEALTH_CHECK_INTERVAL:
            logger.info("  🔍 执行 Provider 健康检查...")
            health = check_provider_health()
            for name, ok in health.items():
                if ok:
                    _health.record_success(name)
                else:
                    _health.record_failure(name)
            last_health_check_batch = batch_num
            logger.info(f"  📊 健康状态: {health}")

        available = _health.get_available(list(PROVIDER_INTERVAL.keys()))

        logger.info(
            f"\n{'─' * 50}\n"
            f"📦 批次 {batch_num}  "
            f"[{offset + 1}~{offset + len(batch)}/{total}]  "
            f"已耗时 {elapsed_total / 60:.1f}min"
        )

        succ, fail, pstats = process_batch(batch, available)
        total_success += succ
        total_fail += fail
        conn.commit()

        if offset + BATCH_SIZE < total:
            logger.info(f"  💤 冷却 {BETWEEN_BATCH_DELAY}s ...")
            time.sleep(BETWEEN_BATCH_DELAY)

    elapsed = time.time() - t0
    rate = total_success / elapsed * 60 if elapsed > 0 else 0

    cur = conn.cursor()
    cur.execute('SELECT COUNT(*) FROM kline_cache WHERE end >= ?', (END_DATE,))
    final_up = cur.fetchone()[0]
    cur.execute('SELECT COUNT(*) FROM kline_cache')
    final_total = cur.fetchone()[0]
    cur.execute(
        'SELECT COUNT(ticker) FROM kline_cache GROUP BY ticker HAVING MAX(end) < ?',
        (END_DATE,),
    )
    final_stale = len(cur.fetchall())

    logger.info(f"\n{'═' * 50}")
    logger.info("✅ 同步完成!")
    logger.info(f"  本进程: 成功={total_success}  失败={total_fail}")
    logger.info(f"  数据库: {final_up}/{final_total} 达标  仍滞后={final_stale}")
    logger.info(f"  耗时={elapsed / 60:.1f}min  速率={rate:.1f}只/min")
    logger.info(f"{'═' * 50}")

    conn.close()
    return 0 if final_stale == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
