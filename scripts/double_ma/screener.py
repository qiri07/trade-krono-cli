"""double_ma 选股 — 核心筛选逻辑。

包含 run_lite（轻量模式）、run_full（完整模式）、process_ticker（单只处理）。
"""

from __future__ import annotations

import json
import pickle
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
from loguru import logger

from scripts._utils import (
    get_all_tickers,
    get_cache_db_path,
    get_sqlite_conn,
    is_bse_or_hk,
    is_etf_ticker,
    ticker_to_thscode,
)
from scripts.double_ma.cards import _print_top30
from scripts.double_ma.constants import (
    API_KEY,
    MA_FAST,
    MA_SLOW,
    PE_MAX,
    PE_MIN,
    PROFIT_THRESHOLD,
    ROE_THRESHOLD,
)
from scripts.double_ma.pe import _fill_pe_and_industry
from scripts.double_ma.roe import fetch_roe_from_api, load_profit_from_cache, load_roe_from_cache
from scripts.double_ma.utils import _limit_pct, calc_ma_ratio, calc_macd
from scripts.indicators import calc_ma as _indicators_calc_ma


def process_ticker(
    ticker: str,
    conn: sqlite3.Connection,
    metadata: dict,
    st_set: set[str],
    skip_pe: bool = False,
) -> dict | None:
    """处理单只股票：读取 K 线，计算双均线金叉 + MACD。

    Parameters
    ----------
    ticker : str
        标准化股票键（sh.600519 / sz.000858）
    conn : sqlite3.Connection
        K 线缓存数据库连接
    metadata : dict
        本地元数据（PE/名称/行业/ST 标记）
    st_set : set[str]
        ST 股票集合
    skip_pe : bool
        为 True 时跳过本地 PE 过滤（PE 缺失保留为 None，
        由调用方后续通过 Fuyao API 补充，避免本地 metadata 过期导致
        技术面达标股票被整体丢弃）

    Returns
    -------
    dict | None
        筛选结果字典，不通过时返回 None
    """
    # 排除北交所和港股（缓存键为小写 bj./hk.，不能只匹配大写 .BJ 后缀）
    if is_bse_or_hk(ticker):
        return None

    is_etf = is_etf_ticker(ticker)

    # 查询 K 线数据
    cur = conn.execute(
        "SELECT data FROM kline_cache WHERE ticker = ? ORDER BY created DESC LIMIT 1",
        (ticker,),
    )
    row = cur.fetchone()
    if not row or not row[0]:
        return None

    try:
        df = pickle.loads(row[0])
    except Exception as e:
        logger.warning(f"⚠️  读取 {ticker} 缓存失败: {e}")
        return None

    if not isinstance(df, pd.DataFrame) or df.empty:
        return None

    # 转换为标准格式
    df = df.copy()
    if "timestamps" in df.columns:
        df["timestamps"] = pd.to_datetime(df["timestamps"])
        df = df.set_index("timestamps").sort_index()
    elif not isinstance(df.index, pd.DatetimeIndex):
        return None

    if len(df) < 30:
        return None

    # 排除 ST
    if ticker in st_set:
        return None

    # 排除涨跌停
    latest_close = float(df["close"].iloc[-1])
    prev_close = float(df["close"].iloc[-2])
    chg_pct = (latest_close / prev_close - 1) * 100
    bare = ticker.split(".", 1)[-1] if "." in ticker else ticker
    limit_pct = _limit_pct(bare)
    if chg_pct >= limit_pct - 0.05 or chg_pct <= -limit_pct + 0.05:
        return None

    # 日 K 线 MA5 > MA13
    daily_ratio = calc_ma_ratio(df)
    if daily_ratio is None or daily_ratio <= 1.0:
        return None

    # 周 K 线 MA5 > MA13
    try:
        weekly_close = df["close"].resample("W").last().dropna()
        if len(weekly_close) < MA_SLOW:
            return None
        weekly_ratio = calc_ma_ratio(weekly_close.to_frame("close"), MA_FAST, MA_SLOW)
    except Exception as e:
        logger.warning(f"⚠️  计算周线均率失败（{ticker}）: {e}")
        weekly_ratio = None

    if weekly_ratio is None or weekly_ratio <= 1.0:
        return None

    # MACD 日线 > 0
    macd_val = calc_macd(df["close"])
    if macd_val is None or macd_val <= 0:
        return None

    # PE 过滤（skip_pe 时本地缺 PE 不丢弃，保留 None 由调用方后续 API 补充）
    # ETF 无基本面指标，直接豁免 PE 门槛（纯技术面参与）
    pe = None
    if not is_etf:
        meta = metadata.get(ticker, {})
        if isinstance(meta, dict):
            for key in ["pe_ttm", "pe"]:
                if key in meta:
                    try:
                        pe = float(meta[key])
                    except (ValueError, TypeError):
                        pe = None
                    break
        if pe is not None and (pe <= PE_MIN or pe >= PE_MAX):
            return None
        if pe is None and not skip_pe:
            return None

    # 收集结果（ROE 稍后补充）
    latest_date = df.index[-1].strftime("%Y-%m-%d")
    latest_close = float(df["close"].iloc[-1])
    meta = metadata.get(ticker, {})
    name = meta.get("name", "") if isinstance(meta, dict) else ""
    industry = (meta.get("industry") or "N/A") if isinstance(meta, dict) else "N/A"

    result = {
        "ticker": ticker,
        "name": name,
        "industry": industry,
        "latest_date": latest_date,
        "latest_close": round(latest_close, 2),
        "pe_ttm": round(pe, 2) if pe is not None else None,
        "daily_ma5": round(float(_indicators_calc_ma(df["close"], MA_FAST).iloc[-1]), 2),
        "daily_ma13": round(float(_indicators_calc_ma(df["close"], MA_SLOW).iloc[-1]), 2),
        "daily_ratio": round(daily_ratio, 4),
        "weekly_ma5": round(float(_indicators_calc_ma(weekly_close, MA_FAST).iloc[-1]), 2),
        "weekly_ma13": round(float(_indicators_calc_ma(weekly_close, MA_SLOW).iloc[-1]), 2),
        "weekly_ratio": round(weekly_ratio, 4),
        "macd": round(macd_val, 6),
        "roe": None,  # 待填充
    }
    if is_etf:
        # ETF 无基本面指标，纯技术面参与；PE 保持 None
        result["is_etf"] = True
    return result


def run_lite(
    output_file: Path,
    skip_pe: bool = False,
) -> list[dict]:
    """轻量模式：仅 MA+PE+MACD 技术分析筛选，保存结果并打印 TOP 30。

    Parameters
    ----------
    output_file : Path
        结果 JSON 输出路径
    skip_pe : bool
        为 True 时本地 metadata 缺 PE 的股票不丢弃（pe_ttm 保留 None），
        技术面扫描完成后从 Fuyao API 批量补充 PE/名称/行业，
        再把 PE 仍缺失或超界的股票剔除。用于本地 metadata 过期的场景。

    Returns
    -------
    list[dict]
        通过筛选的股票列表（已按 PE 升序排列）
    """
    db_path = get_cache_db_path()
    conn = get_sqlite_conn(db_path)

    from scripts._utils import load_metadata_index

    cache_dir = Path("outputs/cache")
    metadata = load_metadata_index(cache_dir, include_bare_keys=False)
    st_set = {t for t, info in metadata.items() if isinstance(info, dict) and info.get("is_st")}
    logger.info(f"元数据: {len(metadata)} 条, ST 股票: {len(st_set)} 只")

    all_tickers = get_all_tickers()
    logger.info(f"总股票数: {len(all_tickers)}")

    results: list[dict] = []
    skipped_no_data = 0

    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = {
            executor.submit(process_ticker, t, conn, metadata, st_set, skip_pe): t
            for t in all_tickers
        }
        for i, future in enumerate(as_completed(futures), 1):
            ticker = futures[future]
            try:
                result = future.result()
                if result is not None:
                    results.append(result)
                else:
                    skipped_no_data += 1
            except Exception as e:
                logger.debug(f"{ticker} 处理异常: {e}")
            if i % 500 == 0:
                logger.info(f"已处理 {i}/{len(all_tickers)} 只...")

    conn.close()

    # skip_pe 模式：本地 metadata 过期（条目极少/PE 全空）时，
    # 技术面达标的股票 pe_ttm 为 None，需从 Fuyao API 补充后再做 PE 界内过滤
    if skip_pe:
        missing_pe = sum(1 for s in results if s.get("pe_ttm") is None)
        if missing_pe:
            logger.info(
                f"\n技术面通过 {len(results)} 只，其中 {missing_pe} 只本地缺 PE，开始 API 补充..."
            )
            _fill_pe_and_industry(results)
        before = len(results)
        results = [
            s
            for s in results
            if s.get("is_etf")
            or (s.get("pe_ttm") is not None and PE_MIN < s["pe_ttm"] < PE_MAX)
        ]
        if len(results) < before:
            logger.info(
                f"  PE 界内过滤(0,{PE_MAX}): 剔除 {before - len(results)} 只"
                f"（PE 缺失或越界），剩余 {len(results)} 只（含 ETF 纯技术面）"
            )

    results.sort(key=lambda x: (x.get("is_etf") or x.get("pe_ttm") is None, x.get("pe_ttm") or 0))

    etf_count = sum(1 for s in results if s.get("is_etf"))
    logger.info("=" * 60)
    logger.info("筛选完成！")
    logger.info(f"  总扫描: {len(all_tickers)} 只")
    logger.info(
        f"  通过双均线金叉+PE+MACD 过滤: {len(results)} 只（含 ETF {etf_count} 只纯技术面）"
    )
    logger.info(f"  - 筛选失败（无数据/不达标）: {skipped_no_data}")
    logger.info("=" * 60)

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    logger.info(f"结果已保存: {output_file}")

    _print_top30(results, with_roe=False)
    return results


def run_full(
    output_file: Path,
    phase1_file: Path,
) -> list[dict]:
    """完整模式：两阶段筛选（MA+MACD → PE → ROE+净利润过滤）。

    Phase 1 技术面筛选时本地 metadata（PE/名称/行业）缺失不再丢弃股票，
    而是保留 pe_ttm=None，随后从同花顺 Fuyao API 批量补充 PE/名称/行业，
    避免本地 metadata 过期（条目极少）导致筛选结果为 0。

    Parameters
    ----------
    output_file : Path
        最终结果 JSON 输出路径
    phase1_file : Path
        第一阶段结果 JSON 路径（供独立 ROE 过滤脚本复用）

    Returns
    -------
    list[dict]
        最终通过全部条件的股票列表
    """
    logger.info("=" * 60)
    logger.info("双均线金叉筛选器启动（完整模式）")
    logger.info(
        f"  筛选条件：日+周 MA{MA_FAST}>MA{MA_SLOW}，排除 ST，"
        f"PE∈({PE_MIN},{PE_MAX})，MACD>0，ROE>{ROE_THRESHOLD}%，"
        f"净利润>{PROFIT_THRESHOLD / 1e8:.0f}亿"
    )
    logger.info("=" * 60)

    # ── Phase 1: 技术面筛选（本地缺 PE 不丢弃，后续 API 补充）───────────
    results = run_lite(phase1_file, skip_pe=True)
    logger.info(f"\nPhase 1 完成: {len(results)} 只通过技术面筛选（MA+MACD）")

    # ── PE/名称/行业 回退：本地 metadata 过期时从 Fuyao API 批量补充 ─────
    if results:
        _fill_pe_and_industry(results)
        before_pe = len(results)
        results = [s for s in results if s.get("pe_ttm") is not None]
        if len(results) < before_pe:
            logger.info(
                f"  PE 仍缺失剔除: {before_pe - len(results)} 只"
                f"（API 补充后剩余 {len(results)} 只）"
            )

    # ── Phase 2: ROE + 净利润 过滤 ──────────────────────────────────────
    logger.info("\nPhase 2: ROE + 净利润 过滤...")

    # 先尝试从缓存加载
    buffett_cache = Path("outputs/cache/buffett_cache.db")
    roe_cache = load_roe_from_cache(buffett_cache)
    profit_cache = load_profit_from_cache(buffett_cache)
    logger.info(f"  缓存 ROE 数据: {len(roe_cache)} 条, 净利润数据: {len(profit_cache)} 条")

    # 补充 API 获取（仅缓存中没有的）
    missing_thscodes = [
        ticker_to_thscode(s["ticker"])
        for s in results
        if s["ticker"].split(".")[1] not in roe_cache
    ]

    api_roe: dict[str, float] = {}
    if missing_thscodes and API_KEY:
        logger.info(f"  需要从 API 获取 ROE: {len(missing_thscodes)} 只")
        with ThreadPoolExecutor(max_workers=20) as exe:
            futures = {
                exe.submit(fetch_roe_from_api, tc): tc  # type: ignore[arg-type]
                for tc in missing_thscodes
            }
            for i, future in enumerate(as_completed(futures), 1):
                thscode = futures[future]
                try:
                    roe_val = future.result()
                except Exception as e:
                    logger.debug(f"{thscode} ROE 获取异常: {e}")
                    continue
                if roe_val is not None:
                    api_roe[thscode] = roe_val  # type: ignore[assignment]
                if i % 50 == 0:
                    logger.info(f"  API ROE 进度: {i}/{len(missing_thscodes)}")

    # 合并 ROE 数据并过滤
    final_results: list[dict] = []
    skipped_roe_missing = 0
    skipped_roe_low = 0
    skipped_profit_missing = 0
    skipped_profit_low = 0

    for s in results:
        # ETF 无基本面指标，技术面达标即入选（完整模式下也保留）
        if s.get("is_etf"):
            final_results.append(s)
            continue

        thscode = ticker_to_thscode(s["ticker"])

        # ROE 检查
        roe = roe_cache.get(thscode) or api_roe.get(thscode)
        if roe is None:
            skipped_roe_missing += 1
            continue
        if roe <= ROE_THRESHOLD:
            skipped_roe_low += 1
            continue

        # 净利润检查（最新年报归母净利润 > 1 亿）
        profit = profit_cache.get(thscode)
        if profit is None:
            skipped_profit_missing += 1
            continue
        if profit <= PROFIT_THRESHOLD:
            skipped_profit_low += 1
            continue

        s["roe"] = round(roe, 2)
        s["net_profit_yi"] = round(profit / 1e8, 2)  # 转换为亿元
        final_results.append(s)

    # 按 PE 升序排列（ETF 无 PE，排最后）
    final_results.sort(key=lambda x: (x.get("is_etf") or x.get("pe_ttm") is None, x.get("pe_ttm") or 0))

    # ── 输出结果 ───────────────────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("筛选完成！")
    logger.info(f"  技术分析通过: {len(results)} 只")
    logger.info(
        f"  **最终通过（含 ROE>{ROE_THRESHOLD}%，净利润>{PROFIT_THRESHOLD / 1e8:.0f}亿）"
        f": {len(final_results)} 只**"
    )
    logger.info(f"  - ROE 缺失: {skipped_roe_missing}")
    logger.info(f"  - ROE≤{ROE_THRESHOLD}%: {skipped_roe_low}")
    logger.info(f"  - 净利润缺失: {skipped_profit_missing}")
    logger.info(f"  - 净利润≤{PROFIT_THRESHOLD / 1e8:.0f}亿: {skipped_profit_low}")
    logger.info("=" * 60)

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(final_results, f, ensure_ascii=False, indent=2)
    logger.info(f"结果已保存: {output_file}")

    _print_top30(final_results, with_roe=True)
    return final_results
