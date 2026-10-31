#!/usr/bin/env python3
"""ETF K 线数据同步脚本 — 发现全市场 ETF 并拉取 K 线入库。

功能：
  1. 用 akshare.fund_etf_category_ths() 发现全市场 ETF 列表（约 1770 只）
  2. 归一化代码为 sh./sz. 前缀（51/58 开头 → sh.，159/156/158 开头 → sz.）
  3. 通过 baostock 拉取 K 线（baostock 原生支持 ETF K 线），增量合并写入缓存
  4. 支持全量历史（--full）和今日增量（默认）两种模式

用法：
  uv run python scripts/etf_history_sync.py              # 今日增量同步
  uv run python scripts/etf_history_sync.py --full       # 全量历史同步（2020 年起）
  uv run python scripts/etf_history_sync.py --codes "510300,159915,518880"  # 指定代码

注意：
  - ETF 无 PE/PB/ROE 等基本面指标，仅 K 线技术分析可用
  - ETF 代码以 51/58/159/156/158 开头，与 A 股代码无重叠（A 股 6/0/3 开头）
  - 同步后的 ETF K 线可被 multi_scan / daily_analysis 等技术分析脚本直接使用
"""

from __future__ import annotations

import argparse
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import re

import pandas as pd
from loguru import logger

from scripts._utils import get_provider_chain
from trade_krono_cli.cache import get_cache
from trade_krono_cli.cli_commands.core import _load_env
from trade_krono_cli.data_providers.factory import get_data_factory

CACHE_DB = Path("outputs/cache/pipeline_cache.db")

# ETF 代码段：沪市 51xxxx/58xxxx，深市 159xxx/156xxx/158xxx
ETF_SH_PREFIXES = ("51", "58")
ETF_SZ_PREFIXES = ("159", "156", "158")

FULL_START_DATE = "2020-01-01"
WORKERS = 8
BATCH_SIZE = 100
FETCH_TIMEOUT = 30
FETCH_PER_TICKER_TIMEOUT = 180  # 单只 ETF 拉取超时（baostock 会话失效重登录后易挂起）


def etf_code_to_ticker(code: str) -> str | None:
    """将 6 位 ETF 代码归一化为带交易所前缀的 ticker。

    51xxxx/58xxxx → sh.<code>，159xxx/156xxx/158xxx → sz.<code>，其他返回 None。
    """
    code = code.zfill(6)
    if code[:2] in ETF_SH_PREFIXES:
        return f"sh.{code}"
    if code[:3] in ETF_SZ_PREFIXES:
        return f"sz.{code}"
    return None


def discover_etf_codes() -> list[str]:
    """发现全市场 ETF 代码列表（6 位裸代码，未归一化）。"""
    import akshare as ak

    logger.info("📡 通过 akshare.fund_etf_category_ths 发现 ETF 列表...")
    try:
        df = ak.fund_etf_category_ths()
        codes = df["基金代码"].astype(str).str.zfill(6).tolist()
        logger.info(f"发现 {len(codes)} 只 ETF（同花顺通道）")
        return codes
    except Exception as e:
        logger.warning(f"同花顺 ETF 列表拉取失败: {e}，尝试东财通道")
        try:
            df = ak.fund_etf_hist_em(symbol="510300")  # 东财通道
            raise RuntimeError("东财通道无法拉取列表，请检查网络")
        except Exception:
            raise


def _load_retry_failed_list(path_str: str) -> list[str]:
    """读取失败清单文件，返回 6 位裸代码列表（支持逗号/换行/带交易所前缀）。"""
    path = Path(path_str)
    if not path.exists():
        raise FileNotFoundError(f"失败清单不存在: {path}")
    text = path.read_text()
    codes: list[str] = []
    for token in re.split(r"[,\n;\s]+", text.strip()):
        token = token.strip()
        if not token:
            continue
        # 去掉交易所前缀（sz./sh.），只保留 6 位裸代码
        bare = token.split(".")[-1] if "." in token else token
        if bare.isdigit() and len(bare) == 6:
            codes.append(bare.zfill(6))
    return codes


def fetch_and_append(
    factory,
    ticker: str,
    start_date: str,
    end_date: str,
) -> tuple[str, int, str]:
    """拉取 ETF K 线并增量合并写入缓存，返回 (ticker, new_rows, provider_name)。

    Provider 链来自 configs/providers.yaml 的 etf 段（get_provider_chain），
    缺省回退到 sh./sz. 硬编码链。
    """
    import sqlite3
    from io import BytesIO

    for provider_name in get_provider_chain(ticker):
        try:
            provider = factory.get_provider(provider_name)
            if provider is None:
                continue
            # ETF 无复权概念，统一 adjustflag='1'（与缓存写入约定一致）
            result = provider.fetch_kline(
                ticker, start_date, end_date, frequency="d", adjustflag="1"
            )
            if result is None or result.is_empty:
                continue
            df = result.to_dataframe()
            if len(df) == 0:
                continue

            ts = pd.to_datetime(df["timestamps"])

            # 增量合并
            conn = sqlite3.connect(str(CACHE_DB))
            row = conn.execute(
                "SELECT data FROM kline_cache WHERE ticker = ?", (ticker,)
            ).fetchone()
            conn.close()

            if row:
                try:
                    existing_df = pd.read_pickle(BytesIO(row[0]))
                except Exception:
                    existing_df = pd.DataFrame()

                if not existing_df.empty:
                    cutoff = pd.Timestamp(existing_df["timestamps"].max())
                    new_rows = df[ts > cutoff]
                    if len(new_rows) == 0:
                        return ticker, 0, "up_to_date"
                    combined = pd.concat([existing_df, new_rows], ignore_index=True)
                    combined = combined.sort_values("timestamps").reset_index(drop=True)
                else:
                    combined = df
            else:
                combined = df

            cache = get_cache()
            cache.set_kline(
                ticker,
                combined["timestamps"].min().strftime("%Y-%m-%d"),
                combined["timestamps"].max().strftime("%Y-%m-%d"),
                "d",
                combined,
                ttl=0.0,
                adjustflag="1",  # ETF 无复权概念，统一按 '1' 存储
            )
            return ticker, len(df), provider_name
        except Exception as e:
            logger.debug(f"  {provider_name} 失败 {ticker}: {e}")
            continue

    return ticker, 0, "FAIL"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ETF K 线数据同步")
    parser.add_argument(
        "--full",
        action="store_true",
        help="全量历史同步（2020 年起），默认仅今日增量",
    )
    parser.add_argument(
        "--codes",
        type=str,
        default="",
        help="指定 ETF 代码（逗号分隔），跳过自动发现。如：510300,159915,518880",
    )
    parser.add_argument(
        "--retry-failed",
        type=str,
        default="",
        help="仅重试上次失败清单文件（etf_failed_YYYYMMDD_HHMMSS.txt，逗号/换行分隔）",
    )
    parser.add_argument(
        "--start",
        type=str,
        default=FULL_START_DATE,
        help=f"全量模式起始日期（默认 {FULL_START_DATE}）",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    _load_env()

    # 确定待同步代码
    if args.codes.strip():
        raw_codes = [c.strip() for c in args.codes.split(",") if c.strip()]
        source_label = "手动指定"
    elif args.retry_failed.strip():
        raw_codes = _load_retry_failed_list(args.retry_failed)
        source_label = f"失败清单 {args.retry_failed}"
    else:
        raw_codes = discover_etf_codes()
        source_label = "自动发现"

    tickers: list[str] = []
    skipped = 0
    for c in raw_codes:
        t = etf_code_to_ticker(c)
        if t is None:
            skipped += 1
            logger.warning(f"  ⚠️  无法识别的 ETF 代码，跳过: {c}")
        else:
            tickers.append(t)
    logger.info(f"📋 {source_label} {len(tickers)} 只 ETF（跳过 {skipped} 只无法识别）: {tickers[:10]}...")
    if not tickers:
        logger.warning("无 ETF 可同步，退出")
        return

    # 确定拉取窗口
    today = time.strftime("%Y-%m-%d")
    if args.full:
        start_date = args.start
        logger.info(f"🚀 全量历史同步（{start_date} ~ {today}）")
    else:
        # 今日增量：拉最近 30 天（含节假日缓冲）
        from datetime import timedelta
        start_date = (
            pd.Timestamp(today) - timedelta(days=30)
        ).strftime("%Y-%m-%d")
        logger.info(f"🚀 今日增量同步（{start_date} ~ {today}）")

    factory = get_data_factory()
    all_batches = [
        tickers[i : i + BATCH_SIZE] for i in range(0, len(tickers), BATCH_SIZE)
    ]

    total_ok = 0
    total_fail = 0
    total_up_to_date = 0
    total_new_rows = 0
    failed_list: list[tuple[str, str]] = []

    for batch_idx, batch in enumerate(all_batches):
        t0 = time.time()
        ok = 0
        fail = 0
        up_to_date = 0
        new_rows = 0
        batch_failed: list[tuple[str, str]] = []

        with ThreadPoolExecutor(max_workers=WORKERS) as executor:
            futures = {
                executor.submit(fetch_and_append, factory, t, start_date, today): t
                for t in batch
            }
            try:
                for future in as_completed(
                    futures,
                    timeout=(FETCH_PER_TICKER_TIMEOUT * len(batch)) // WORKERS + 60,
                ):
                    ticker, n_rows, detail = future.result()
                    if detail == "up_to_date":
                        up_to_date += 1
                    elif detail == "FAIL":
                        fail += 1
                        batch_failed.append((ticker, detail))
                    else:
                        ok += 1
                        new_rows += n_rows
            except TimeoutError:
                # 单只拉取挂起（baostock 会话失效重登录卡住）时整批兜底，
                # 未完成 future 标记 batch_timeout，避免 as_completed 无限等待
                timed_out = [
                    t for f, t in futures.items() if not f.done()
                ]
                for t in timed_out:
                    batch_failed.append((t, "batch_timeout"))
                fail += len(timed_out)
                logger.warning(
                    f"  批次 {batch_idx + 1}/{len(all_batches)} 超时兜底："
                    f"{len(timed_out)} 只未完成（疑似 baostock 会话挂起）"
                )

        elapsed = time.time() - t0
        total_ok += ok
        total_fail += fail
        total_up_to_date += up_to_date
        total_new_rows += new_rows
        failed_list.extend(batch_failed)
        logger.info(
            f"  批次 {batch_idx + 1}/{len(all_batches)}: "
            f"成功 {ok}, 已最新 {up_to_date}, 失败 {fail}, "
            f"新增 {new_rows} 行, 耗时 {elapsed:.1f}s"
        )

    logger.info("\n" + "=" * 60)
    logger.info(
        f"完成！成功 {total_ok}，已最新 {total_up_to_date}，"
        f"失败 {total_fail}，总计 {len(tickers)}"
    )
    if failed_list:
        sample = ", ".join(t for t, _ in failed_list[:20])
        logger.warning(f"失败股票 ({len(failed_list)}): {sample}...")
        fail_file = Path("outputs/results") / f"etf_failed_{time.strftime('%Y%m%d_%H%M%S')}.txt"
        fail_file.parent.mkdir(parents=True, exist_ok=True)
        fail_file.write_text(",".join(t for t, _ in failed_list))
        logger.info(f"失败清单已写入 {fail_file}（可用 --retry-failed 重试）")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
