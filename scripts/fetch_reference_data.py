#!/usr/bin/env python3
"""批量拉取股票元数据（参考数据）。

通过同花顺 API 获取所有股票的名称、交易所后缀等基础信息，
写入 outputs/cache/metadata.json，供分析脚本使用。

支持断点续传（checkpoint）：
  全量模式每批完成后将进度写入 outputs/cache/metadata_checkpoint.json，
  中断后再次运行自动从 checkpoint 续拉（跳过已成功、重试失败/None）。

用法：
  uv run python scripts/fetch_reference_data.py
  uv run python scripts/fetch_reference_data.py --tickers "000001,002027"
  uv run python scripts/fetch_reference_data.py --force-refresh
  uv run python scripts/fetch_reference_data.py --force-refresh --resume
  uv run python scripts/fetch_reference_data.py --reset-checkpoint
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger

from scripts._utils import get_all_tickers, normalize_ticker_key
from trade_krono_cli.cli_commands.core import _load_env
from trade_krono_cli.data_providers.factory import get_data_factory

WORKERS = 20
BATCH_SIZE = 100
FETCH_TIMEOUT = 15
FETCH_PER_TICKER_TIMEOUT = 180  # 单只元数据拉取超时（网络读挂起时兜底，避免批次阻塞）
METADATA_FILE = Path("outputs/cache/metadata.json")
CHECKPOINT_FILE = Path("outputs/cache/metadata_checkpoint.json")

_load_env()


def _fetch_metadata_one(factory, ticker: str) -> tuple[str, dict | None, str]:
    """拉取单只股票的元数据，返回标准化键（始终带交易所前缀）。"""
    try:
        meta = factory.fetch_metadata(ticker)
        key = normalize_ticker_key(ticker)
        if meta is None:
            return (key, None, "none")
        return (
            key,
            {
                "ticker": meta.ticker,
                "industry": meta.industry,
                "pe_ttm": meta.pe_ttm,
                "pb": meta.pb,
                "ipo_date": meta.ipo_date,
                "is_st": meta.is_st,
                "source": meta.source,
            },
            "ok",
        )
    except Exception as e:
        return (normalize_ticker_key(ticker), None, f"error: {str(e)[:50]}")


def _fetch_metadata_one_hard(factory, ticker: str) -> tuple[str, dict | None, str]:
    """带硬超时的单只元数据拉取。

    真正的网络拉取（factory.fetch_metadata）可能因底层 provider（尤其 baostock
    常驻 TCP 会话、SDK 无读超时）永久 hang。若直接在 worker 线程里调用，
    该线程永不返回 → ThreadPoolExecutor.shutdown(wait=True) 永久阻塞整批。

    这里把真正的 fetch 丢进一个 daemon 线程，worker 线程用 join(timeout) 等待：
    · 完成 → 取回结果
    · 超时 → 放弃该 daemon 线程（进程退出时自动回收），返回 "timeout" 状态
    这样 worker 线程本身有界退出，线程池销毁不再被拖死。
    """
    holder: dict = {}

    def _run() -> None:
        result = _fetch_metadata_one(factory, ticker)
        holder["result"] = result

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(FETCH_PER_TICKER_TIMEOUT)

    if "result" not in holder:
        # daemon 线程仍在卡着（baostock 会话挂起）；放弃它，标记超时
        logger.warning(
            f"⏱️  硬超时 {FETCH_PER_TICKER_TIMEOUT}s，放弃 {ticker}（底层会话未释放，进程退出时回收）"
        )
        return (normalize_ticker_key(ticker), None, "timeout")

    return holder["result"]


def _load_existing_metadata() -> dict[str, dict | None]:
    """读取现有 metadata.json；文件不存在或损坏时返回空 dict。

    Returns:
        {归一化 ticker: metadata dict 或 None} 映射
    """
    if not METADATA_FILE.exists():
        return {}
    try:
        data = json.loads(METADATA_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"⚠️ 读取现有 metadata.json 失败，将全量重建: {str(e)[:80]}")
        return {}


# ── 断点续传 checkpoint ──────────────────────────────────────────────────
def load_checkpoint() -> dict[str, dict | None]:
    """读取 checkpoint 文件；不存在或损坏时返回空 dict。

    Returns:
        {归一化 ticker: metadata dict 或 None} 映射
    """
    if not CHECKPOINT_FILE.exists():
        return {}
    try:
        data = json.loads(CHECKPOINT_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"⚠️ 读取 checkpoint 失败，将全量重拉: {str(e)[:80]}")
        return {}


def save_checkpoint(results: dict[str, dict | None]) -> None:
    """将当前进度原子写入 checkpoint 文件（先写临时文件再 rename，避免写一半损坏）。

    Args:
        results: {归一化 ticker: metadata dict 或 None} 当前已完成的映射
    """
    CHECKPOINT_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = CHECKPOINT_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, CHECKPOINT_FILE)


def clear_checkpoint() -> None:
    """删除 checkpoint 文件（全量完成时调用）。"""
    try:
        CHECKPOINT_FILE.unlink(missing_ok=True)
    except OSError as e:
        logger.warning(f"⚠️ 清除 checkpoint 失败: {e}")


def metadata_age_hours() -> float | None:
    """metadata.json 距上次更新的小时数；文件不存在返回 None。"""
    if not METADATA_FILE.exists():
        return None
    return (time.time() - METADATA_FILE.stat().st_mtime) / 3600.0


def refresh_metadata_if_stale(max_age_hours: float = 24.0) -> bool:
    """metadata.json 超过 max_age_hours 未更新时触发全量刷新。

    每日流程第 1 步调用，保证 PE/PB/行业数据新鲜（默认 24h 未刷新则重拉全量）。

    Args:
        max_age_hours: 刷新阈值（小时），默认 24。

    Returns:
        True 执行了刷新，False 跳过（够新或异常外无副作用）。
    """
    age = metadata_age_hours()
    if age is not None and age < max_age_hours:
        logger.info(f"✅ metadata.json {age:.1f}h 前已更新（<{max_age_hours:.0f}h），跳过刷新")
        return False
    reason = "不存在" if age is None else f"{age:.1f}h 前更新"
    logger.info(f"📊 metadata.json {reason}，开始全量刷新")
    # TTL 触发时若有 checkpoint（上次中断），自动续传；否则全量重拉
    has_checkpoint = load_checkpoint()
    main(tickers_input=None, resume=bool(has_checkpoint))
    return True


def main(
    tickers_input: list[str] | None = None,
    resume: bool = False,
    use_checkpoint: bool = True,
) -> None:
    """批量拉取股票元数据（参考数据）并保存到 JSON 文件。

    部分模式（指定 --tickers）合并进现有 metadata.json，不覆盖全量；
    全量模式（默认）重建整个文件，支持断点续传（checkpoint）。

    Args:
        tickers_input: 指定股票代码列表，为 None 时拉取全量。
        resume: 为 True 时从 checkpoint 续拉（跳过已成功，重试失败/None）。
        use_checkpoint: 为 True 时每批完成后写 checkpoint，支持中断后续拉。
    """
    logger.info("📊 启动参考数据（元数据）拉取")

    tickers = tickers_input or get_all_tickers()
    partial = tickers_input is not None

    # 断点续传：从 checkpoint 恢复已成功部分，仅重拉失败/未完成的
    checkpoint: dict[str, dict | None] = {}
    if partial:
        results: dict[str, dict | None] = _load_existing_metadata()
    else:
        results = {}
        if use_checkpoint:
            checkpoint = load_checkpoint()
            if checkpoint:
                # 仅全量模式下用 checkpoint 恢复
                results.update(checkpoint)
                done = sum(1 for v in checkpoint.values() if v is not None)
                logger.info(
                    f"📍 检测到 checkpoint（{len(checkpoint)} 只，其中 {done} 只已成功），"
                    f"跳过已成功，仅重拉 {len(checkpoint) - done} 只失败/未完成"
                )

    logger.info(
        f"📋 共 {len(tickers)} 只股票待获取元数据"
        + ("（部分合并模式）" if partial else "（全量模式）")
        + (f"（断点续传，已恢复 {len(results)} 只）" if (not partial and results) else "")
    )

    # 若 resume 且有 checkpoint，仅拉取 checkpoint 中失败/None 或未记录的 ticker
    if resume and use_checkpoint and not partial and results:
        pending = [
            t
            for t in tickers
            if normalize_ticker_key(t) not in results or results[normalize_ticker_key(t)] is None
        ]
        if pending:
            logger.info(f"⏩ 断点续传：仅重拉 {len(pending)} 只失败/未完成")
            tickers = pending
        else:
            logger.info("⏩ 断点续传：全部 ticker 已在 checkpoint 中成功，无需重拉")
            tickers = []

    factory = get_data_factory()
    success = 0
    failed = 0
    start_time = time.time()

    total_batches = max(1, (len(tickers) + BATCH_SIZE - 1) // BATCH_SIZE)
    for batch_start in range(0, len(tickers), BATCH_SIZE):
        batch = tickers[batch_start : batch_start + BATCH_SIZE]
        batch_num = batch_start // BATCH_SIZE + 1
        logger.info(
            f"📦 批次 {batch_num}/{total_batches} "
            f"[{batch_start + 1}~{batch_start + len(batch)}/{len(tickers)}]"
        )

        with ThreadPoolExecutor(max_workers=WORKERS) as pool:
            futures = {
                pool.submit(_fetch_metadata_one_hard, factory, t): t for t in batch
            }
            for future in as_completed(futures):
                ticker = futures[future]
                # _fetch_metadata_one_hard 内部已对真正的网络拉取做硬超时兜底，
                # 这里再留一层 future.result 超时作为保险（正常不会触发）
                try:
                    key, meta, status = future.result(
                        timeout=FETCH_PER_TICKER_TIMEOUT + 30
                    )
                except TimeoutError:
                    key = normalize_ticker_key(ticker)
                    results[key] = None
                    failed += 1
                    logger.warning(f"⏱️  批次级超时跳过: {ticker}")
                    continue
                results[key] = meta
                if status == "ok":
                    success += 1
                else:
                    failed += 1

        # 每批完成后写 checkpoint（全量模式且启用 checkpoint 时）
        if use_checkpoint and not partial:
            save_checkpoint(results)

        time.sleep(1)

    elapsed = time.time() - start_time
    logger.info(f"\n{'=' * 60}")
    logger.info("✅ 参考数据拉取完成!")
    logger.info(f"  成功: {success}, 失败: {failed}")
    logger.info(f"  耗时: {elapsed:.1f}s ({elapsed / 60:.1f}min)")

    # 保存元数据到 JSON
    METADATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    if partial:
        # 部分模式：合并进现有文件
        existing = _load_existing_metadata()
        existing.update(results)
        results = existing
    # 全量模式写入前清理旧 metadata 中的 key（防止 key 格式不一致的残留数据
    # 被并入新结果，例如旧版无交易所前缀的裸代码 key 混入带前缀的新 key）
    elif results:
        stale = [k for k in _load_existing_metadata() if k not in results]
        for k in stale:
            results.setdefault(k, None)
    METADATA_FILE.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"  已保存至: {METADATA_FILE}")

    # 全量模式完成后清除 checkpoint
    if use_checkpoint and not partial:
        clear_checkpoint()
        logger.info("  🧹 checkpoint 已清除")
    logger.info(f"{'=' * 60}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="批量拉取股票元数据（参考数据）")
    parser.add_argument("--tickers", default=None, help="股票代码（逗号分隔），空=全量（部分拉取合并进现有文件）")
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="忽略 24h TTL，强制全量刷新",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="断点续传：从 checkpoint 恢复，仅重拉失败/未完成的 ticker",
    )
    parser.add_argument(
        "--reset-checkpoint",
        action="store_true",
        help="清除 checkpoint 并从头开始（与 --resume 互斥）",
    )
    args = parser.parse_args()

    if args.reset_checkpoint:
        clear_checkpoint()
        logger.info("🧹 checkpoint 已清除，将全量重拉")
        main(tickers_input=None, resume=False)
    elif args.force_refresh:
        main(tickers_input=None, resume=args.resume)
    else:
        tickers_list = (
            [t.strip() for t in args.tickers.split(",") if t.strip()] if args.tickers else None
        )
        if tickers_list:
            main(tickers_input=tickers_list)
        else:
            refresh_metadata_if_stale()
