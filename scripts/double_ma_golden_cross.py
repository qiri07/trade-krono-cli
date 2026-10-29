"""双均线金叉筛选器（统一版）：日+周 MA5>MA13，排除ST，PE(0,30)，MACD日线>0

两档模式：
  - 轻量模式（--lite）：仅技术分析 MA+PE+MACD（原 double_ma_screener.py 功能）
  - 完整模式（默认）：两阶段筛选，技术分析后追加 ROE>10% + 净利润>1亿
    （ROE 优先读取 buffett_cache.db，缺失部分从同花顺 Fuyao API 补充）

用法：
  # 完整模式（默认）：双均线金叉 + ROE + 净利润
  uv run python scripts/double_ma_golden_cross.py

  # 轻量模式：仅 MA+PE+MACD
  uv run python scripts/double_ma_golden_cross.py --lite
"""

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
    is_etf_ticker,
    normalize_ticker_key,
    ticker_to_thscode,
)

# ── 配置 ──────────────────────────────────────────────────────────────────────
API_KEY = (os.getenv("HITHINK_FINANCE_API_KEY", "") or os.getenv("FUYAO_API_KEY", "")).strip()
HEADERS = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
BASE = "https://fuyao.aicubes.cn"

# 筛选阈值
MA_FAST, MA_SLOW = 5, 13
PE_MIN, PE_MAX = 0, 30
ROE_THRESHOLD = 10.0


# ── 工具函数 ──────────────────────────────────────────────────────────────────


def load_metadata(cache_dir: Path) -> dict:
    """加载PE/行业/ST元数据，统一键格式为标准前缀（sh.600xxx / sz.000xxx）。

    metadata.json 中可能同时存在 "sh.600519" 和 "600519" 两种键格式，
    此函数将所有键归一化，避免查询时因格式不匹配导致数据丢失。
    """
    meta_path = cache_dir / "metadata.json"
    if meta_path.exists():
        with open(meta_path, encoding="utf-8") as f:
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
    conn = sqlite3.connect(db_path, check_same_thread=False, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL")

    cache_dir = Path("outputs/cache")
    metadata = load_metadata(cache_dir)
    st_set = get_st_tickers(metadata)
    logger.info(f"元数据: {len(metadata)} 条, ST股票: {len(st_set)} 只")

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
                f"（PE缺失或越界），剩余 {len(results)} 只（含ETF纯技术面）"
            )

    results.sort(key=lambda x: (x.get("is_etf") or x.get("pe_ttm") is None, x.get("pe_ttm") or 0))

    etf_count = sum(1 for s in results if s.get("is_etf"))
    logger.info("=" * 60)
    logger.info("筛选完成！")
    logger.info(f"  总扫描: {len(all_tickers)} 只")
    logger.info(f"  通过双均线金叉+PE+MACD过滤: {len(results)} 只（含ETF {etf_count} 只纯技术面）")
    logger.info(f"  - 筛选失败（无数据/不达标）: {skipped_no_data}")
    logger.info("=" * 60)

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    logger.info(f"结果已保存: {output_file}")

    _print_top30(results, with_roe=False)
    return results


def _print_top30(results: list[dict], *, with_roe: bool) -> None:
    """打印 TOP 30 股票表格。

    Parameters
    ----------
    results : list[dict]
        筛选结果列表
    with_roe : bool
        是否包含 ROE / 净利润列（完整模式）
    """
    top_n = min(30, len(results))
    logger.info(f"\nTOP {top_n} 股票:")
    if with_roe:
        logger.info(
            f"{'序号':<4} {'代码':<10} {'名称':<10} {'行业':<10} "
            f"{'收盘':<8} {'PE':<6} {'ROE%':<6} {'利润亿':<6} {'日Ratio':<8} {'周Ratio':<8} {'MACD':<10}"
        )
        logger.info("-" * 110)
        for i, s in enumerate(results[:top_n], 1):
            logger.info(
                f"{i:<4} {s['ticker']:<10} {s['name'] or 'N/A':<10} {s['industry'] or 'N/A':<10} "
                f"{s['latest_close']:<8.2f} {s['pe_ttm'] or 0:<6.2f} "
                f"{s.get('roe') or 0:<6.2f} {s.get('net_profit_yi') or 0:<6.2f} "
                f"{s['daily_ratio']:<8.4f} {s['weekly_ratio']:<8.4f} {s['macd']:<10.6f}"
            )
    else:
        logger.info(
            f"{'序号':<4} {'代码':<10} {'名称':<10} {'行业':<10} "
            f"{'收盘':<8} {'PE':<6} {'日Ratio':<8} {'周Ratio':<8} {'MACD':<10}"
        )
        logger.info("-" * 90)
        for i, s in enumerate(results[:top_n], 1):
            logger.info(
                f"{i:<4} {s['ticker']:<10} {s['name'] or 'N/A':<10} {s['industry'] or 'N/A':<10} "
                f"{s['latest_close']:<8.2f} {s['pe_ttm'] or 0:<6.2f} "
                f"{s['daily_ratio']:<8.4f} {s['weekly_ratio']:<8.4f} {s['macd']:<10.6f}"
            )


def _fetch_pe_batch_api(thscodes: list[str]) -> dict[str, dict]:
    """从同花顺 Fuyao API 批量获取 PE/名称/行业（单批最多约 100 只）。

    请求使用 X-api-key 头（快照接口不使用 Bearer 认证）。返回
    {六位代码: {"pe_ttm", "name", "industry"}} 映射；无 API_KEY 或
    请求失败时返回空字典（调用方自行降级处理）。

    Parameters
    ----------
    thscodes : list[str]
        同花顺标准代码列表（形如 600519.SH / 000858.SZ）
    """
    if not API_KEY or not thscodes:
        return {}
    pe_map: dict[str, dict] = {}
    for i in range(0, len(thscodes), 100):
        batch = ",".join(thscodes[i : i + 100])
        try:
            resp = requests.get(
                f"{BASE}/api/a-share/valuations/snapshot",
                params={"thscodes": batch},
                headers={"X-api-key": API_KEY},
                timeout=30,
            )
            data = resp.json()
            if data.get("code") != 0:
                continue
            for item in data.get("data", {}).get("item", []):
                ths = item.get("thscode", "")
                code6 = ths.split(".")[0] if "." in ths else ths
                try:
                    pe_val = float(item.get("pe_ttm"))
                except (ValueError, TypeError):
                    pe_val = None
                if pe_val is not None:
                    pe_map[code6] = {
                        "pe_ttm": pe_val,
                        "name": (item.get("name") or "").strip(),
                        "industry": item.get("industry", ""),
                    }
        except Exception as e:
            logger.warning(f"  Fuyao API 批次 {i // 100 + 1} 失败: {str(e)[:80]}")
    return pe_map


def _backfill_pe_from_api(results: list[dict]) -> int:
    """就地补充列表中 pe_ttm 缺失股票的 PE/名称/行业（Fuyao API）。

    仅处理 pe_ttm 为 None 的条目；已有本地 PE 的条目不动。

    Parameters
    ----------
    results : list[dict]
        股票字典列表（需含 ticker / name / industry / pe_ttm 键）

    Returns
    -------
    int
        成功补充 PE 的股票数量
    """
    missing = [s for s in results if s.get("pe_ttm") is None]
    if not missing:
        return 0
    if not API_KEY:
        logger.warning(f"  PE 回退: Fuyao API_KEY 未配置，{len(missing)} 只股票 PE 保持缺失")
        return 0

    logger.info(f"  PE 回退: 从 Fuyao API 补充 {len(missing)} 只股票的 PE/行业...")
    thscodes = [ticker_to_thscode(s["ticker"]) for s in missing]
    pe_map = _fetch_pe_batch_api(thscodes)

    filled = 0
    for s in missing:
        code6 = s["ticker"].split(".")[1]
        info = pe_map.get(code6)
        if info and info.get("pe_ttm") is not None:
            s["pe_ttm"] = info["pe_ttm"]
            if not s.get("name"):
                s["name"] = info.get("name", "")
            if s.get("industry") in (None, "", "N/A"):
                s["industry"] = info.get("industry") or "N/A"
            filled += 1
    logger.info(
        f"  PE 回退完成: 补充 {filled}/{len(missing)} 只，剩余缺失 {len(missing) - filled} 只"
    )
    return filled


def _fill_pe_and_industry(results: list[dict]) -> None:
    """两阶段：先补名称/行业（全量批次），再对 PE 仍缺失的补 PE。

    本地 metadata 过期（条目极少）时，名称/行业全空，PE 也无值。
    先用快照接口全量补充名称/行业，再对 PE 仍缺失的补充 PE，
    避免把 700+ 只全部塞进单个 API 请求。

    Parameters
    ----------
    results : list[dict]
        技术面通过的股票列表（就地修改 name / industry / pe_ttm）
    """
    need_name = [s for s in results if not s.get("name") or s.get("industry") in (None, "")]
    if need_name:
        logger.info(f"  名称/行业缺失 {len(need_name)} 只，先从 Fuyao API 补充...")
        thscodes = [ticker_to_thscode(s["ticker"]) for s in need_name]
        info_map = _fetch_pe_batch_api(thscodes)
        for s in need_name:
            info = info_map.get(s["ticker"].split(".")[1])
            if not info:
                continue
            if not s.get("name"):
                s["name"] = info.get("name", "")
            if s.get("industry") in (None, "", "N/A"):
                s["industry"] = info.get("industry") or "N/A"
    _backfill_pe_from_api(results)


def process_ticker(
    ticker: str,
    conn: sqlite3.Connection,
    metadata: dict,
    st_set: set[str],
    skip_pe: bool = False,
) -> dict | None:
    """处理单只股票：读取K线，计算双均线金叉+MACD。

    Parameters
    ----------
    ticker : str
        标准化股票键（sh.600519 / sz.000858）
    conn : sqlite3.Connection
        K线缓存数据库连接
    metadata : dict
        本地元数据（PE/名称/行业/ST 标记）
    st_set : set[str]
        ST 股票集合
    skip_pe : bool
        为 True 时跳过本地 PE 过滤（PE 缺失保留为 None，
        由调用方后续通过 Fuyao API 补充，避免本地 metadata 过期导致
        技术面达标股票被整体丢弃）
    """
    # 排除北交所和港股
    if ".BJ" in ticker or ticker.startswith("hk."):
        return None

    is_etf = is_etf_ticker(ticker)

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

    # PE过滤（skip_pe 时本地缺 PE 不丢弃，保留 None 由调用方后续 API 补充）
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

    # 收集结果（ROE稍后补充）
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
        "daily_ma5": round(float(df["close"].rolling(MA_FAST).mean().iloc[-1]), 2),
        "daily_ma13": round(float(df["close"].rolling(MA_SLOW).mean().iloc[-1]), 2),
        "daily_ratio": round(daily_ratio, 4),
        "weekly_ma5": round(float(weekly_close.rolling(MA_FAST).mean().iloc[-1]), 2),
        "weekly_ma13": round(float(weekly_close.rolling(MA_SLOW).mean().iloc[-1]), 2),
        "weekly_ratio": round(weekly_ratio, 4),
        "macd": round(macd_val, 6),
        "roe": None,  # 待填充
    }
    if is_etf:
        # ETF 无基本面指标，纯技术面参与；PE 保持 None
        result["is_etf"] = True
    return result


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
        f"  筛选条件：日+周MA{MA_FAST}>MA{MA_SLOW}，排除ST，"
        f"PE∈({PE_MIN},{PE_MAX})，MACD>0，ROE>{ROE_THRESHOLD}%，"
        f"净利润>{PROFIT_THRESHOLD / 1e8:.0f}亿"
    )
    logger.info("=" * 60)

    # ── Phase 1: 技术面筛选（本地缺 PE 不丢弃，后续 API 补充） ───────────
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
    logger.info(f"  缓存ROE数据: {len(roe_cache)} 条, 净利润数据: {len(profit_cache)} 条")

    # 补充API获取（仅缓存中没有的）
    missing_thscodes = [
        ticker_to_thscode(s["ticker"])
        for s in results
        if s["ticker"].split(".")[1] not in roe_cache
    ]

    api_roe: dict[str, float] = {}
    if missing_thscodes and API_KEY:
        logger.info(f"  需要从API获取ROE: {len(missing_thscodes)} 只")
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
                    logger.debug(f"{thscode} ROE获取异常: {e}")
                    continue
                if roe_val is not None:
                    api_roe[thscode] = roe_val  # type: ignore[assignment]
                if i % 50 == 0:
                    logger.info(f"  API ROE进度: {i}/{len(missing_thscodes)}")

    # 合并ROE数据并过滤
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

    # 按PE升序排列（ETF 无 PE，排最后）
    final_results.sort(key=lambda x: (x.get("is_etf") or x.get("pe_ttm") is None, x.get("pe_ttm") or 0))

    # ── 输出结果 ───────────────────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("筛选完成！")
    logger.info(f"  技术分析通过: {len(results)} 只")
    logger.info(
        f"  **最终通过（含ROE>{ROE_THRESHOLD}%, 净利润>{PROFIT_THRESHOLD / 1e8:.0f}亿）"
        f": {len(final_results)} 只**"
    )
    logger.info(f"  - ROE缺失: {skipped_roe_missing}")
    logger.info(f"  - ROE≤{ROE_THRESHOLD}%: {skipped_roe_low}")
    logger.info(f"  - 净利润缺失: {skipped_profit_missing}")
    logger.info(f"  - 净利润≤{PROFIT_THRESHOLD / 1e8:.0f}亿: {skipped_profit_low}")
    logger.info("=" * 60)

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(final_results, f, ensure_ascii=False, indent=2)
    logger.info(f"结果已保存: {output_file}")

    _print_top30(final_results, with_roe=True)
    return final_results


def main() -> None:
    """命令行入口：--lite 轻量模式（MA+PE+MACD），默认完整模式（+ROE+净利润）。"""
    import argparse

    parser = argparse.ArgumentParser(description="双均线金叉筛选器（统一版）")
    parser.add_argument(
        "--lite",
        action="store_true",
        help="轻量模式：仅 MA+PE+MACD 技术分析（默认完整模式含 ROE+净利润）",
    )
    parser.add_argument(
        "--date",
        default=datetime.now().strftime("%Y-%m-%d"),
        help="分析日期 YYYY-MM-DD，用作结果文件命名（默认今日）",
    )
    args = parser.parse_args()

    date_tag = args.date
    output_dir = Path("outputs/results")
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.lite:
        output_file = output_dir / f"double_ma_golden_cross_pe_macd_{date_tag}.json"
        logger.info("轻量模式：仅 MA+PE+MACD 技术分析筛选")
        results = run_lite(output_file)
        print(f"RESULT_FILE={output_file}")
        print(f"COUNT={len(results)}")
        return

    phase1_file = output_dir / f"double_ma_golden_cross_pe_macd_{date_tag}.json"
    output_file = output_dir / f"double_ma_golden_cross_{date_tag}.json"
    results = run_full(output_file, phase1_file)
    print(f"RESULT_FILE={output_file}")
    print(f"COUNT={len(results)}")


if __name__ == "__main__":
    main()
