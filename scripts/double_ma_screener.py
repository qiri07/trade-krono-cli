"""双均线金叉筛选器：日+周 MA5>MA13，排除ST，PE(0,30)，MACD日线>0"""
from __future__ import annotations

import json
import pickle
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import pandas as pd
from loguru import logger

sys.path.insert(0, "/run/media/onai/MyDisk/Work/trade-krono-cli")

from scripts._utils import get_all_tickers, get_cache_db_path, normalize_ticker_key


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
    st_set: set[str] = set()
    for ticker, info in metadata.items():
        if isinstance(info, dict) and info.get("is_st", False):
            st_set.add(ticker)
    return st_set


def calc_ma_ratio(df: pd.DataFrame, fast: int = 5, slow: int = 13) -> float | None:
    """计算快速均线/慢速均线比值，返回最新值"""
    if len(df) < slow:
        return None
    ma_fast = df["close"].rolling(fast).mean().iloc[-1]
    ma_slow = df["close"].rolling(slow).mean().iloc[-1]
    if ma_slow == 0:
        return None
    return float(ma_fast / ma_slow)


def calc_macd(closes: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> float | None:
    """计算MACD线值（dif），返回最新值。MACD > 0 表示在0轴上方"""
    if len(closes) < slow + signal:
        return None
    ema_fast = closes.ewm(span=fast, adjust=False).mean()
    ema_slow = closes.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    return float(macd_line.iloc[-1])


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

    if len(df) < 30:  # MACD需要更多数据
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
        if len(weekly_close) < 13:
            return None
        weekly_ratio = calc_ma_ratio(weekly_close.to_frame("close"), 5, 13)
    except Exception:
        weekly_ratio = None

    if weekly_ratio is None or weekly_ratio <= 1.0:
        return None

    # MACD日线 > 0（在0轴上方）
    macd_val = calc_macd(df["close"])
    if macd_val is None or macd_val <= 0:
        return None

    # PE过滤
    pe = None
    meta = metadata.get(ticker, {})
    if isinstance(meta, dict):
        pe = meta.get("pe_ttm") or meta.get("pe")
        if pe is not None:
            try:
                pe = float(pe)
            except (ValueError, TypeError):
                pe = None

    if pe is None or pe <= 0 or pe >= 30:
        return None

    # 收集结果
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
        "daily_ma5": round(float(df["close"].rolling(5).mean().iloc[-1]), 2),
        "daily_ma13": round(float(df["close"].rolling(13).mean().iloc[-1]), 2),
        "daily_ratio": round(daily_ratio, 4),
        "weekly_ma5": round(float(weekly_close.rolling(5).mean().iloc[-1]), 2),
        "weekly_ma13": round(float(weekly_close.rolling(13).mean().iloc[-1]), 2),
        "weekly_ratio": round(weekly_ratio, 4),
        "macd": round(macd_val, 6),
    }


def main() -> None:
    date_tag = datetime.now().strftime("%Y-%m-%d")
    output_dir = Path("outputs/results")
    output_dir.mkdir(parents=True, exist_ok=True)
    output_file = output_dir / f"double_ma_golden_cross_pe_macd_{date_tag}.json"

    logger.info("开始筛选（日+周MA5>MA13，排除ST，PE∈(0,30)，MACD日线>0）...")

    # 连接数据库
    db_path = get_cache_db_path()
    conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL")

    # 加载元数据
    cache_dir = Path("outputs/cache")
    metadata = load_metadata(cache_dir)
    st_set = get_st_tickers(metadata)
    logger.info(f"元数据: {len(metadata)} 条, ST股票: {len(st_set)} 只")

    # 获取所有股票列表
    all_tickers = get_all_tickers()
    logger.info(f"总股票数: {len(all_tickers)}")

    # 并行处理
    results: list[dict] = []
    skipped_no_data = 0

    max_workers = 20
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
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
                    skipped_no_data += 1
            except Exception as e:
                logger.warning(f"{ticker} 处理异常: {e}")

            if i % 500 == 0:
                logger.info(f"已处理 {i}/{len(all_tickers)} 只...")

    conn.close()

    # 按PE升序排列
    results.sort(key=lambda x: x["pe_ttm"])

    # 统计
    logger.info("=" * 60)
    logger.info("筛选完成！")
    logger.info(f"  总扫描: {len(all_tickers)} 只")
    logger.info(f"  通过双均线金叉+PE+MACD过滤: {len(results)} 只")
    logger.info(f"  - 筛选失败（无数据/不达标）: {skipped_no_data}")
    logger.info("=" * 60)

    # 保存结果
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    logger.info(f"结果已保存: {output_file}")

    # 打印前30名
    logger.info(f"\nTOP {min(30, len(results))} 股票:")
    logger.info(
        f"{'序号':<4} {'代码':<10} {'名称':<10} {'行业':<10} "
        f"{'收盘':<8} {'PE':<6} {'日Ratio':<8} {'周Ratio':<8} {'MACD':<10}"
    )
    logger.info("-" * 90)
    for i, s in enumerate(results[:30], 1):
        logger.info(
            f"{i:<4} {s['ticker']:<10} {s['name'] or 'N/A':<10} {s['industry'] or 'N/A':<10} "
            f"{s['latest_close']:<8.2f} {s['pe_ttm']:<6.2f} "
            f"{s['daily_ratio']:<8.4f} {s['weekly_ratio']:<8.4f} {s['macd']:<10.6f}"
        )

    print(f"RESULT_FILE={output_file}")
    print(f"COUNT={len(results)}")


if __name__ == "__main__":
    main()
