"""
数据源速度基准测试（只读，不修改任何代码）。

测试内容：
  - 冷启动时间（首次初始化）
  - 热调用时间（已初始化后）
  - 连续调用稳定性与频率限制

用法：uv run python tests/bench_data_sources.py
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Optional

from loguru import logger

from trade_krono_cli.data_providers.factory import DataProviderFactory

TICKER = "sh.600519"
START = "2025-01-01"
END = "2026-08-30"
N_RUNS = 5  # 每个provider热调用次数


@dataclass
class BenchResult:
    provider: str
    cold_time: Optional[float]  # 首次调用耗时（含初始化）
    warm_times: list[float]
    errors: list[str]
    rows_returned: int = 0


def _timing(fn, label: str) -> tuple[float, Optional[Exception], Any]:
    t0 = time.perf_counter()
    try:
        result = fn()
        dt = time.perf_counter() - t0
        return dt, None, result
    except Exception as e:
        dt = time.perf_counter() - t0
        return dt, e, None


async def bench_provider(
    factory: DataProviderFactory, name: str, ticker: str, start: str, end: str, n_runs: int
) -> BenchResult:
    result = BenchResult(provider=name, cold_time=None, warm_times=[], errors=[])

    # 直接获取指定 provider 实例
    provider = factory.get_provider(name)
    if provider is None:
        result.errors.append("provider不可用（未安装/未配置）")
        logger.warning(f"⚠️  {name} 不可用，跳过")
        return result

    # 1. 冷启动
    def do_cold():
        df = provider.fetch_kline(ticker, start, end)
        return df

    dt, err, df = _timing(do_cold, f"{name} cold")
    if err:
        result.errors.append(f"cold: {err}")
        logger.warning(f"❌ {name} 冷启动失败: {err}")
        return result
    result.cold_time = dt
    result.rows_returned = df.length if df is not None else 0
    logger.info(f"✅ {name} 冷启动: {dt:.3f}s, 返回 {result.rows_returned} 行")

    # 小憩，让连接稳定
    await asyncio.sleep(0.3)

    # 2. 热调用
    for i in range(n_runs):

        def do_hot():
            df = provider.fetch_kline(ticker, start, end)
            return df

        dt, err, df = _timing(do_hot, f"{name} hot-{i}")
        if err:
            result.errors.append(f"hot[{i}]: {err}")
            logger.warning(f"⚠️  {name} 热调用[{i}]失败: {err}")
        else:
            result.warm_times.append(dt)
            logger.info(f"   {name} 热调用[{i}]: {dt:.4f}s")
        # 频率限制：每次调用间隔
        await asyncio.sleep(0.2)

    return result


async def main():
    logger.info("=" * 60)
    logger.info(f"数据源速度基准测试  ticker={TICKER}  {START}~{END}")
    logger.info(f"每源冷启动1次 + 热调用{N_RUNS}次，间隔200ms防封")
    logger.info("=" * 60)

    factory = DataProviderFactory()

    providers = ["baostock", "mootdx", "tushare", "tonghuashun"]
    results: list[BenchResult] = []

    for prov in providers:
        r = await bench_provider(factory, prov, TICKER, START, END, N_RUNS)
        results.append(r)

    # ── 汇总报告 ──
    logger.info("")
    logger.info("=" * 60)
    logger.info("基准测试结果汇总")
    logger.info("=" * 60)
    logger.info(
        f"{'Provider':<14} {'冷启动(s)':<12} {'热调用均值(s)':<14} {'热调用最快(s)':<14} {'热调用最慢(s)':<14} {'错误数':<6} {'行数'}"
    )
    logger.info("-" * 90)
    for r in results:
        if r.warm_times:
            avg = sum(r.warm_times) / len(r.warm_times)
            mn = min(r.warm_times)
            mx = max(r.warm_times)
        else:
            avg = mn = mx = float("nan")
        err_cnt = len(r.errors)
        flag = " ❌" if err_cnt else ""
        logger.info(
            f"{r.provider:<14} "
            f"{r.cold_time:<12.3f} "
            f"{avg:<14.4f} "
            f"{mn:<14.4f} "
            f"{mx:<14.4f} "
            f"{err_cnt:<6} "
            f"{r.rows_returned}{flag}"
        )

    logger.info("-" * 90)
    # 排名
    valid = [r for r in results if r.warm_times]
    if valid:
        ranked = sorted(valid, key=lambda r: sum(r.warm_times) / len(r.warm_times))
        logger.info("")
        logger.info("🏆 热调用速度排名（从快到慢）:")
        for i, r in enumerate(ranked, 1):
            avg = sum(r.warm_times) / len(r.warm_times)
            logger.info(f"  {i}. {r.provider:<12} 平均 {avg:.4f}s  冷启动 {r.cold_time:.3f}s")

    if results and all(r.errors for r in results):
        logger.warning("⚠️  所有数据源均有错误，请检查网络/API配置")


if __name__ == "__main__":
    asyncio.run(main())
