"""双均线金叉筛选器：日+周 MA5>MA13，排除ST，PE(0,30)，MACD日线>0，ROE>15%"""
from __future__ import annotations

import json
import os
import pickle
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests
from loguru import logger

sys.path.insert(0, "/run/media/onai/MyDisk/Work/trade-krono-cli")

from scripts._utils import (
    get_all_tickers,
    get_cache_db_path,
    normalize_ticker_key,
    ticker_to_thscode,
)

# ── 配置 ──────────────────────────────────────────────────────────────────────
API_KEY = (
    os.getenv("HITHINK_FINANCE_API_KEY", "")
    or os.getenv("FUYAO_API_KEY", "")
).strip()
HEADERS = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
BASE = "https://fuyao.aicubes.cn"

# 筛选阈值
MA_FAST, MA_SLOW = 5, 13
PE_MIN, PE_MAX = 0, 30
ROE_THRESHOLD = 15.0


# ── 工具函数 ──────────────────────────────────────────────────────────────────


def load_metadata(cache_dir: Path) -> dict:
    """加载PE/行业/ST元数据，统一键格式为标准前缀（sh.600xxx / sz.000xxx）。

    metadata.json 中可能同时存在 "sh.600519" 和 "600519" 两种键格式，
    此函数将所有键归一化，避免查询时因格式不匹配导致数据丢失。
    """
    meta_path = cache_dir / "metadata.json"
    if meta_path.exists():
        with open(meta_path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return {normalize_ticker_key(k): v for k, v in raw.items()}
    return {}


def get_st_tickers(metadata: dict) -> set[str]:
    """从元数据中提取ST股票列表"""
    return {t for t, info in metadata.items() if isinstance(info, dict) and info.get("is_st")}


def calc_ma_ratio(df: pd.DataFrame, fast: int = MA_FAST, slow: int = MA_SLOW) -> float | None:
    """计算快速均线/慢速均线比值"""
    if len(df) < slow:
        return None
    ma_fast = df["close"].rolling(fast).mean().iloc[-1]
    ma_slow = df["close"].rolling(slow).mean().iloc[-1]
    if ma_slow == 0:
        return None
    return float(ma_fast / ma_slow)


def calc_macd(closes: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> float | None:
    """计算MACD线值（dif），MACD > 0 表示在0轴上方"""
    if len(closes) < slow + signal:
        return None
    ema_fast = closes.ewm(span=fast, adjust=False).mean()
    ema_slow = closes.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    return float(macd_line.iloc[-1])


def fetch_roe_from_api(thscode: str) -> float | None:
    """从同花顺Fuyao API获取最新ROE"""
    if not API_KEY:
        return None
    try:
        resp = requests.post(
            f"{BASE}/api/a-share/financials/indicators",
            headers=HEADERS,
            json={"thscode": thscode, "report": "latest"},
            timeout=8,
        )
        data = resp.json()
        if data.get("code") != 0:
            return None
        for ab in data.get("data", {}).get("abilities", []):
            for ind in ab.get("indicators", []):
                if ind.get("index_id") == "index_weighted_avg_roe":
                    try:
                        return float(ind.get("value"))
                    except (ValueError, TypeError):
                        continue
        return None
    except Exception:  # noqa: BLE001
        return None


def load_roe_from_cache(buffett_cache_path: Path) -> dict[str, float]:
    """从巴菲特缓存数据库加载ROE数据"""
    if not buffett_cache_path.exists():
        return {}
    conn = sqlite3.connect(buffett_cache_path)
    rows = conn.execute("SELECT key, data FROM cache_financials").fetchall()
    conn.close()

    roe_map: dict[str, float] = {}
    for key, data_str in rows:
        if not key.startswith("fin_"):
            continue
        parts = key.split("_")
        if len(parts) < 2:
            continue
        thscode = parts[1]
        try:
            d = json.loads(data_str)
            roe = d.get("roe")
            if roe is not None and roe > 0:
                if thscode not in roe_map or roe > roe_map[thscode]:
                    roe_map[thscode] = roe
        except (json.JSONDecodeError, TypeError):
            continue
    return roe_map


def load_profit_from_cache(buffett_cache_path: Path) -> dict[str, float]:
    """从巴菲特缓存数据库加载最新净利润（parent_holder_net_profit，单位：元）"""
    if not buffett_cache_path.exists():
        return {}
    conn = sqlite3.connect(buffett_cache_path)
    rows = conn.execute("SELECT key, data FROM cache_income").fetchall()
    conn.close()

    profit_map: dict[str, float] = {}
    for key, data_str in rows:
        if not key.startswith("income_"):
            continue
        parts = key.split("_", 1)
        if len(parts) < 2:
            continue
        thscode = parts[1]  # e.g. "601838.SH"
        try:
            d = json.loads(data_str)
            items = d.get("items", [])
            if not items:
                continue
            # 按 fiscal_year 降序取最新
            items.sort(key=lambda x: x.get("fiscal_year", 0), reverse=True)
            net_profit = items[0].get("parent_holder_net_profit")
            if net_profit is not None:
                try:
                    profit_map[thscode] = float(net_profit)
                except (ValueError, TypeError):
                    pass
        except (json.JSONDecodeError, TypeError):
            continue
    return profit_map


# ── 阈值常量 ───────────────────────────────────────────────────────────────────
PROFIT_THRESHOLD = 1e8  # 1亿元 = 100,000,000 元


# ── 核心筛选逻辑 ───────────────────────────────────────────────────────────────
def process_ticker(
    ticker: str,
    conn: sqlite3.Connection,
    metadata: dict,
    st_set: set[str],
) -> dict | None:
    """处理单只股票：读取K线，计算双均线金叉+MACD"""
    # 排除北交所和港股
    if ".BJ" in ticker or ticker.startswith("hk."):
        return None

    # 查询K线数据
    cur = conn.execute(
        "SELECT data FROM kline_cache WHERE ticker = ? ORDER BY created DESC LIMIT 1",
        (ticker,),
    )
    row = cur.fetchone()
    if not row or not row[0]:
        return None

    try:
        df = pickle.loads(row[0])
    except Exception:
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

    # 排除ST
    if ticker in st_set:
        return None

    # 日K线 MA5 > MA13
    daily_ratio = calc_ma_ratio(df)
    if daily_ratio is None or daily_ratio <= 1.0:
        return None

    # 周K线 MA5 > MA13
    try:
        weekly_close = df["close"].resample("W").last().dropna()
        if len(weekly_close) < MA_SLOW:
            return None
        weekly_ratio = calc_ma_ratio(weekly_close.to_frame("close"), MA_FAST, MA_SLOW)
    except Exception:
        weekly_ratio = None

    if weekly_ratio is None or weekly_ratio <= 1.0:
        return None

    # MACD日线 > 0
    macd_val = calc_macd(df["close"])
    if macd_val is None or macd_val <= 0:
        return None

    # PE过滤
    pe = None
    meta = metadata.get(ticker, {})
    if isinstance(meta, dict):
        for key in ["pe_ttm", "pe"]:
            if key in meta:
                try:
                    pe = float(meta[key])
                except (ValueError, TypeError):
                    pe = None
                break

    if pe is None or pe <= PE_MIN or pe >= PE_MAX:
        return None

    # 收集结果（ROE稍后补充）
    latest_date = df.index[-1].strftime("%Y-%m-%d")
    latest_close = float(df["close"].iloc[-1])
    name = meta.get("name", "") if isinstance(meta, dict) else ""
    industry = (meta.get("industry") or "N/A") if isinstance(meta, dict) else "N/A"

    return {
        "ticker": ticker,
        "name": name,
        "industry": industry,
        "latest_date": latest_date,
        "latest_close": round(latest_close, 2),
        "pe_ttm": round(pe, 2),
        "daily_ma5": round(float(df["close"].rolling(MA_FAST).mean().iloc[-1]), 2),
        "daily_ma13": round(float(df["close"].rolling(MA_SLOW).mean().iloc[-1]), 2),
        "daily_ratio": round(daily_ratio, 4),
        "weekly_ma5": round(float(weekly_close.rolling(MA_FAST).mean().iloc[-1]), 2),
        "weekly_ma13": round(float(weekly_close.rolling(MA_SLOW).mean().iloc[-1]), 2),
        "weekly_ratio": round(weekly_ratio, 4),
        "macd": round(macd_val, 6),
        "roe": None,  # 待填充
    }


def main() -> None:
    date_tag = datetime.now().strftime("%Y-%m-%d")
    output_dir = Path("outputs/results")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"double_ma_golden_cross_{date_tag}.json"

    logger.info("=" * 60)
    logger.info("双均线金叉筛选器启动")
    logger.info(f"  筛选条件：日+周MA{MA_FAST}>MA{MA_SLOW}，排除ST，PE∈({PE_MIN},{PE_MAX})，MACD>0，ROE>{ROE_THRESHOLD}%，净利润>{PROFIT_THRESHOLD/1e8:.0f}亿")
    logger.info("=" * 60)

    # ── Phase 1: 技术分析筛选 ──────────────────────────────────────────────
    db_path = get_cache_db_path()
    conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL")

    cache_dir = Path("outputs/cache")
    metadata = load_metadata(cache_dir)
    st_set = get_st_tickers(metadata)
    logger.info(f"元数据: {len(metadata)} 条, ST股票: {len(st_set)} 只")

    all_tickers = get_all_tickers()
    logger.info(f"总股票数: {len(all_tickers)}")

    results: list[dict] = []
    stats = {"no_data": 0}

    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = {
            executor.submit(process_ticker, t, conn, metadata, st_set): t
            for t in all_tickers
        }
        for i, future in enumerate(as_completed(futures), 1):
            ticker = futures[future]
            try:
                result = future.result()
                if result is not None:
                    results.append(result)
                else:
                    stats["no_data"] += 1
            except Exception as e:
                logger.debug(f"{ticker} 处理异常: {e}")

            if i % 500 == 0:
                logger.info(f"Phase 1 进度: {i}/{len(all_tickers)}")

    conn.close()

    logger.info(f"\nPhase 1 完成: {len(results)} 只通过技术分析筛选")
    logger.info(f"  筛选失败（无数据/不达标）: {stats['no_data']}")

    # ── Phase 2: ROE + 净利润 过滤 ───────────────────────────────────────
    logger.info("\nPhase 2: ROE + 净利润 过滤...")

    # 先尝试从缓存加载
    buffett_cache = Path("outputs/cache/buffett_cache.db")
    roe_cache = load_roe_from_cache(buffett_cache)
    profit_cache = load_profit_from_cache(buffett_cache)
    logger.info(f"  缓存ROE数据: {len(roe_cache)} 条, 净利润数据: {len(profit_cache)} 条")

    # 补充API获取（仅缓存中没有的）
    missing_thscodes = [ticker_to_thscode(s["ticker"]) for s in results if s["ticker"].split(".")[1] not in roe_cache]

    if missing_thscodes and API_KEY:
        logger.info(f"  需要从API获取ROE: {len(missing_thscodes)} 只")
        api_roe: dict[str, float] = {}
        with ThreadPoolExecutor(max_workers=20) as exe:
            # mypy 在裸 except 函数内对 future.result() 的返回类型推断为 dict[Any, Any] | None，
            # 但 fetch_roe_from_api 显式声明返回 float | None。类型标注已正确，此处忽略 mypy 误报。
            futures = {
                exe.submit(fetch_roe_from_api, tc): tc  # type: ignore[arg-type]
                for tc in missing_thscodes
            }
            for i, future in enumerate(as_completed(futures), 1):
                thscode = futures[future]
                try:
                    roe_val = future.result()
                except Exception:
                    continue
                if roe_val is not None:
                    api_roe[thscode] = roe_val  # type: ignore[assignment]  # mypy 误判 future.result() 返回 dict，实际为 float | None
                if i % 50 == 0:
                    logger.info(f"  API ROE进度: {i}/{len(missing_thscodes)}")
    else:
        api_roe = {}

    # 合并ROE数据并过滤
    final_results: list[dict] = []
    skipped_roe_missing = 0
    skipped_roe_low = 0
    skipped_profit_missing = 0
    skipped_profit_low = 0

    for s in results:
        thscode = ticker_to_thscode(s["ticker"])

        # ROE检查
        roe = roe_cache.get(thscode) or api_roe.get(thscode)
        if roe is None:
            skipped_roe_missing += 1
            continue
        if roe <= ROE_THRESHOLD:
            skipped_roe_low += 1
            continue

        # 净利润检查（最新年报归母净利润 > 1亿）
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

    # 按PE升序排列
    final_results.sort(key=lambda x: x["pe_ttm"])

    # ── 输出结果 ───────────────────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("筛选完成！")
    logger.info(f"  总扫描: {len(all_tickers)} 只")
    logger.info(f"  技术分析通过: {len(results)} 只")
    logger.info(f"  **最终通过（含ROE> {ROE_THRESHOLD}%, 净利润>{PROFIT_THRESHOLD/1e8:.0f}亿）: {len(final_results)} 只**")
    logger.info(f"  - ROE缺失: {skipped_roe_missing}")
    logger.info(f"  - ROE≤{ROE_THRESHOLD}%: {skipped_roe_low}")
    logger.info(f"  - 净利润缺失: {skipped_profit_missing}")
    logger.info(f"  - 净利润≤{PROFIT_THRESHOLD/1e8:.0f}亿: {skipped_profit_low}")
    logger.info("=" * 60)

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(final_results, f, ensure_ascii=False, indent=2)
    logger.info(f"结果已保存: {output_file}")

    # 打印TOP 30
    logger.info(f"\nTOP {min(30, len(final_results))} 股票:")
    logger.info(
        f"{'序号':<4} {'代码':<10} {'名称':<10} {'行业':<10} "
        f"{'收盘':<8} {'PE':<6} {'ROE%':<6} {'利润亿':<6} {'日Ratio':<8} {'周Ratio':<8} {'MACD':<10}"
    )
    logger.info("-" * 90)
    for i, s in enumerate(final_results[:30], 1):
        logger.info(
            f"{i:<4} {s['ticker']:<10} {s['name'] or 'N/A':<10} {s['industry'] or 'N/A':<10} "
            f"{s['latest_close']:<8.2f} {s['pe_ttm']:<6.2f} {s['roe']:<6.2f} {s['net_profit_yi']:<6.2f} "
            f"{s['daily_ratio']:<8.4f} {s['weekly_ratio']:<8.4f} {s['macd']:<10.6f}"
        )

    print(f"RESULT_FILE={output_file}")
    print(f"COUNT={len(final_results)}")


if __name__ == "__main__":
    main()
