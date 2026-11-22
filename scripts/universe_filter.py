#!/usr/bin/env python3
"""scripts/universe_filter.py — 全市场统一前置股票池过滤。

在「数据获取完成」之后、「所有选股流程」之前执行，把原先只在最后汇集阶段
（``combined_stock_analysis.py`` 的 A股硬过滤）或各脚本里零散实现的硬门槛
统一前移，避免低流动性 / 停牌 / 次新 / 低价 / 小市值股票先跑完全部分析、
最后才被剔除，浪费 API 与 AI 调用。

过滤口径（单一来源，各选股线共用同一份结果）：

  - 市场：仅 A 股（剔除港股、北交所、ETF）
  - ST / *ST / 退市（名称关键词 + 元数据 is_st）
  - 次新：上市不足 30 个自然日（无 ipo_date 时按 K 线根数 < 20 判定）
  - 停牌：近 30 个交易日成交量为 0 的天数 > 5，或最新 K 线滞后超 5 个自然日
  - 低价：最新收盘价 < 1 元
  - 小市值：总市值 < 20 亿元（腾讯实时行情，缺失时不剔除，仅计数）
  - 低流动性：近 20 日日均成交额 < 5000 万元（缺失时不剔除，仅计数）

产出 ``outputs/results/universe_YYYYMMDD.json``，各选股脚本通过
:func:`apply_universe_filter` 取交集；文件缺失时回退到脚本原有逻辑（可灰度）。

用法::

    uv run python scripts/universe_filter.py                    # 最近交易日
    uv run python scripts/universe_filter.py --date 2026-11-20
    uv run python scripts/universe_filter.py --min-market-cap 20
    uv run python scripts/universe_filter.py --require-market-cap
"""

from __future__ import annotations

import argparse
import json
import pickle
import sqlite3
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any  # 行情/K 线字段值类型混杂（float/int/str/None），Any 为合理

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
from loguru import logger

from scripts._utils import (
    ST_NAME_KEYWORDS,
    expected_trading_day,
    get_cache_db_path,
    get_sqlite_conn,
    is_st_name,
    load_metadata_index,
    market_key,
)

# ── 文件命名 ────────────────────────────────────────────────────────────────
UNIVERSE_FILE_PREFIX = "universe_"

# ── 硬门槛默认值 ────────────────────────────────────────────────────────────
# 市值下限按用户要求统一为 20 亿元（原 _utils.MIN_MARKET_CAP_YI=50 仅供
# macd_ma/double_ma 等旧脚本使用，统一前置池按 20 亿执行）。
MIN_LISTING_DAYS = 30
MIN_LISTING_BARS = 20
MAX_SUSPENSION_DAYS_30D = 5
MIN_PRICE = 1.0
MIN_AVG_AMOUNT_20D = 50_000_000.0
MAX_STALE_CALENDAR_DAYS = 5
DEFAULT_MIN_MARKET_CAP_YI = 20.0
# 行情文件最多回看天数（周末/节假日/手动重放时容忍旧文件）
UNIVERSE_MAX_AGE_DAYS = 7

# ── 剔除原因键（供统计与审计）──────────────────────────────────────────────
REASON_LABELS: dict[str, str] = {
    "hk": "港股",
    "bse": "北交所",
    "etf": "ETF",
    "st": "ST/退市",
    "new_listing": "次新",
    "suspended": "停牌",
    "stale": "行情滞后",
    "low_price": "低价",
    "low_market_cap": "市值不足",
    "low_liquidity": "流动性不足",
    "no_data": "无 K 线数据",
    "invalid_code": "代码非法",
}


@dataclass(frozen=True)
class UniverseRules:
    """前置过滤阈值（CLI 可覆盖，写入快照便于审计）。"""

    min_market_cap_yi: float = DEFAULT_MIN_MARKET_CAP_YI
    min_listing_days: int = MIN_LISTING_DAYS
    min_listing_bars: int = MIN_LISTING_BARS
    max_suspension_days_30d: int = MAX_SUSPENSION_DAYS_30D
    min_price: float = MIN_PRICE
    min_avg_amount_20d: float = MIN_AVG_AMOUNT_20D
    max_stale_calendar_days: int = MAX_STALE_CALENDAR_DAYS
    require_market_cap: bool = False

    def to_dict(self) -> dict[str, float | int | bool]:
        """转为可 JSON 序列化的字典。"""
        return asdict(self)


@dataclass
class TickerMetrics:
    """单只股票的过滤指标（市值/名称来自实时行情，其余来自本地 K 线）。"""

    ticker: str
    name: str = ""
    bars: int = 0
    last_date: str = ""
    close: float | None = None
    avg_amount_20d: float | None = None
    suspension_days_30d: int = 0
    market_cap_yi: float | None = None
    pe_ttm: float | None = None
    pb: float | None = None
    turnover: float | None = None
    listing_days: int | None = None
    reasons: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        """无任何剔除原因即通过。"""
        return not self.reasons

    def to_dict(self) -> dict[str, Any]:
        """转为可 JSON 序列化的字典。"""
        return {
            "name": self.name,
            "bars": self.bars,
            "last_date": self.last_date,
            "close": self.close,
            "avg_amount_20d": self.avg_amount_20d,
            "suspension_days_30d": self.suspension_days_30d,
            "market_cap_yi": self.market_cap_yi,
            "pe_ttm": self.pe_ttm,
            "pb": self.pb,
            "turnover": self.turnover,
            "listing_days": self.listing_days,
            "reasons": self.reasons,
        }


# ─────────────────────────────────────────────────────────────────────────────
# K 线指标
# ─────────────────────────────────────────────────────────────────────────────


def _normalize_kline(df: pd.DataFrame) -> pd.DataFrame | None:
    """统一 K 线时间列并升序排序，缺列时返回 None。"""
    if df is None or df.empty:
        return None
    ts_col = "timestamps" if "timestamps" in df.columns else None
    if ts_col is None and "timestamp" in df.columns:
        ts_col = "timestamp"
    if ts_col is None or "close" not in df.columns:
        return None
    df = df.copy()
    df[ts_col] = pd.to_datetime(df[ts_col])
    df = df.sort_values(ts_col).reset_index(drop=True)
    if ts_col != "timestamps":
        df = df.rename(columns={ts_col: "timestamps"})
    return df


def compute_kline_metrics(
    df: pd.DataFrame | None,
    date_str: str,
    *,
    min_rows: int = 1,
) -> dict[str, Any]:
    """从单只股票日线计算过滤所需指标。

    Args:
        df: 日线 DataFrame（需含 ``timestamps``/``timestamp`` 与 ``close``）。
        date_str: 分析日期（YYYY-MM-DD），晚于该日期的行会被忽略。
        min_rows: 少于该行数时视为无有效数据。

    Returns:
        ``{"bars", "last_date", "close", "avg_amount_20d", "suspension_days_30d"}``。
    """
    out: dict[str, Any] = {
        "bars": 0,
        "last_date": "",
        "close": None,
        "avg_amount_20d": None,
        "suspension_days_30d": 0,
    }
    kline = _normalize_kline(df)
    if kline is None:
        return out
    kline = kline.loc[kline["timestamps"] <= pd.Timestamp(date_str)]
    if len(kline) < min_rows:
        return out

    close = kline["close"].astype(float)
    out["bars"] = int(len(kline))
    out["last_date"] = kline["timestamps"].iloc[-1].strftime("%Y-%m-%d")
    out["close"] = float(close.iloc[-1])

    amount_col = "amount" if "amount" in kline.columns else None
    if amount_col is not None:
        amounts = kline[amount_col].astype(float)
    elif "volume" in kline.columns:
        # 无成交额列时用 收盘价 × 成交量 估算（与 a_stock_screen 口径一致）
        amounts = close * kline["volume"].astype(float)
    else:
        amounts = None
    if amounts is not None and len(amounts) > 0:
        out["avg_amount_20d"] = float(amounts.tail(20).mean())

    if "volume" in kline.columns:
        volumes = kline["volume"].astype(float).tail(30)
        out["suspension_days_30d"] = int((volumes <= 0).sum())

    return out


def _read_kline_metrics(
    conn: sqlite3.Connection,
    ticker: str,
    date_str: str,
) -> dict[str, Any]:
    """从 kline_cache 读取单只股票日线并计算指标（读取失败返回空指标）。"""
    row = conn.execute(
        "SELECT data FROM kline_cache WHERE ticker = ? ORDER BY end DESC LIMIT 1",
        (ticker,),
    ).fetchone()
    if not row or row[0] is None:
        return compute_kline_metrics(None, date_str)
    try:
        df = pd.read_pickle(BytesIO(row[0]))
    except (OSError, ValueError, EOFError, pickle.UnpicklingError) as exc:
        logger.debug(f"{ticker} K 线反序列化失败: {exc}")
        return compute_kline_metrics(None, date_str)
    return compute_kline_metrics(df, date_str)


# ─────────────────────────────────────────────────────────────────────────────
# 门槛判定
# ─────────────────────────────────────────────────────────────────────────────


def _stale_days(date_str: str, last_date: str) -> int:
    """最新 K 线距分析日期的自然日数（无数据返回极大值）。"""
    if not last_date:
        return 10**6
    try:
        delta = datetime.strptime(date_str, "%Y-%m-%d") - datetime.strptime(
            last_date, "%Y-%m-%d"
        )
    except ValueError:
        return 10**6
    return max(0, delta.days)


def evaluate_ticker(
    metrics: TickerMetrics,
    date_str: str,
    rules: UniverseRules,
    *,
    is_st: bool = False,
) -> list[str]:
    """按硬门槛逐条判定，返回剔除原因列表（空列表表示通过）。

    Args:
        metrics: 单只股票的指标（已含实时行情字段）。
        date_str: 分析日期（YYYY-MM-DD）。
        rules: 过滤阈值。
        is_st: 元数据标记的 ST（与名称关键词取并集）。

    Returns:
        剔除原因键列表，如 ``["new_listing", "low_liquidity"]``。
    """
    reasons: list[str] = []

    if metrics.bars <= 0 or metrics.close is None:
        reasons.append("no_data")
        return reasons

    if is_st or is_st_name(metrics.name, ST_NAME_KEYWORDS):
        reasons.append("st")

    if metrics.listing_days is not None:
        if metrics.listing_days < rules.min_listing_days:
            reasons.append("new_listing")
    elif metrics.bars < rules.min_listing_bars:
        reasons.append("new_listing")

    if metrics.suspension_days_30d > rules.max_suspension_days_30d:
        reasons.append("suspended")
    elif _stale_days(date_str, metrics.last_date) > rules.max_stale_calendar_days:
        reasons.append("stale")

    if metrics.close < rules.min_price:
        reasons.append("low_price")

    if metrics.market_cap_yi is not None:
        if metrics.market_cap_yi < rules.min_market_cap_yi:
            reasons.append("low_market_cap")
    elif rules.require_market_cap:
        reasons.append("low_market_cap")

    if (
        metrics.avg_amount_20d is not None
        and metrics.avg_amount_20d < rules.min_avg_amount_20d
    ):
        reasons.append("low_liquidity")

    return reasons


def _listing_days_from_meta(meta: dict[str, object], date_str: str) -> int | None:
    """从元数据 ipo_date 推算上市自然日数（无 ipo_date 返回 None）。"""
    ipo = meta.get("ipo_date")
    if not isinstance(ipo, str) or not ipo.strip():
        return None
    try:
        return (datetime.strptime(date_str, "%Y-%m-%d") - datetime.strptime(ipo[:10], "%Y-%m-%d")).days
    except ValueError:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# 快照构建
# ─────────────────────────────────────────────────────────────────────────────


def _fetch_quotes(tickers: list[str]) -> dict[str, Any]:
    """通过腾讯批量行情取名称 / 市值 / PE / PB（失败时返回空字典）。"""
    if not tickers:
        return {}
    try:
        from trade_krono_cli.data_providers.tencent_provider import (  # noqa: PLC0415
            TencentProvider,
        )

        quotes = TencentProvider().fetch_quotes_batch(tickers)
    except Exception as exc:  # noqa: BLE001 - 行情源异常不应阻断股票池构建
        logger.warning(f"⚠️  腾讯批量行情获取失败，市值/名称将缺失: {exc}")
        return {}
    logger.info(f"📈 腾讯行情快照：{len(quotes)}/{len(tickers)} 只")
    return quotes


def build_universe(
    date_str: str | None = None,
    *,
    rules: UniverseRules | None = None,
    quotes: dict[str, Any] | None = None,
    metadata: dict[str, dict[str, object]] | None = None,
    db_path: Path | None = None,
) -> dict[str, Any]:
    """构建统一前置股票池快照。

    Args:
        date_str: 分析日期（YYYY-MM-DD），缺省为最近交易日。
        rules: 过滤阈值，缺省使用模块默认值（市值 20 亿等）。
        quotes: 可注入的实时行情（``ticker -> RealtimeQuote``），便于测试；
            ``None`` 时通过腾讯批量行情拉取。
        metadata: 元数据索引，缺省 :func:`load_metadata_index`。
        db_path: 缓存数据库路径，缺省 :func:`get_cache_db_path`。

    Returns:
        可直接写盘的快照字典（date / rules / universe / rejected / stats）。
    """
    rules = rules or UniverseRules()
    date_str = date_str or expected_trading_day()
    metadata = metadata if metadata is not None else load_metadata_index()
    db_path = db_path or get_cache_db_path()

    conn = get_sqlite_conn(db_path)
    try:
        rows = conn.execute("SELECT DISTINCT ticker FROM kline_cache").fetchall()
        all_tickers = sorted(str(r[0]) for r in rows)

        rejected: dict[str, list[str]] = {}
        a_tickers: list[str] = []
        for ticker in all_tickers:
            key = market_key(ticker)
            if key in ("hk", "bse", "etf"):
                rejected.setdefault(key, []).append(ticker)
            elif key == "a" and len(ticker.split(".")[-1]) == 6:
                a_tickers.append(ticker)
            else:
                rejected.setdefault("invalid_code", []).append(ticker)

        logger.info(
            f"📊 前置股票池：缓存 {len(all_tickers)} 只，A股候选 {len(a_tickers)} 只，"
            f"按市场剔除 港股 {len(rejected.get('hk', []))} / 北交所 {len(rejected.get('bse', []))}"
            f" / ETF {len(rejected.get('etf', []))}"
        )

        if quotes is None:
            quotes = _fetch_quotes(a_tickers)

        metrics_map: dict[str, TickerMetrics] = {}
        for ticker in a_tickers:
            km = _read_kline_metrics(conn, ticker, date_str)
            meta = metadata.get(ticker) or metadata.get(ticker.split(".")[-1]) or {}
            if not isinstance(meta, dict):
                meta = {}
            quote = quotes.get(ticker)
            name = str(getattr(quote, "name", "") or meta.get("name") or "").strip()
            m = TickerMetrics(
                ticker=ticker,
                name=name,
                bars=int(km["bars"]),
                last_date=str(km["last_date"]),
                close=km["close"],
                avg_amount_20d=km["avg_amount_20d"],
                suspension_days_30d=int(km["suspension_days_30d"]),
                market_cap_yi=(
                    float(quote.market_cap)
                    if quote is not None and getattr(quote, "market_cap", None) is not None
                    else None
                ),
                pe_ttm=(
                    float(quote.pe)
                    if quote is not None and getattr(quote, "pe", None) is not None
                    else None
                ),
                pb=(
                    float(quote.pb)
                    if quote is not None and getattr(quote, "pb", None) is not None
                    else None
                ),
                turnover=(
                    float(quote.turnover)
                    if quote is not None and getattr(quote, "turnover", None) is not None
                    else None
                ),
                listing_days=_listing_days_from_meta(meta, date_str),
            )
            m.reasons = evaluate_ticker(
                m, date_str, rules, is_st=bool(meta.get("is_st"))
            )
            metrics_map[ticker] = m
    finally:
        conn.close()

    universe: list[str] = []
    for ticker, m in metrics_map.items():
        if m.passed:
            universe.append(ticker)
        else:
            for reason in m.reasons:
                rejected.setdefault(reason, []).append(ticker)

    missing_market_cap = sum(
        1 for m in metrics_map.values() if m.market_cap_yi is None
    )
    missing_liquidity = sum(1 for m in metrics_map.values() if m.avg_amount_20d is None)

    rejected_stats: dict[str, int] = {
        k: len(v) for k, v in sorted(rejected.items(), key=lambda x: -len(x[1]))
    }

    stats: dict[str, object] = {
        "cached_tickers": len(all_tickers),
        "a_candidates": len(a_tickers),
        "passed": len(universe),
        "rejected_total": len(a_tickers) - len(universe),
        "rejected": rejected_stats,
        "missing_market_cap": missing_market_cap,
        "missing_liquidity": missing_liquidity,
    }

    logger.info(
        f"✅ 前置股票池：通过 {len(universe)}/{len(a_tickers)} 只"
        f"（剔除 {len(a_tickers) - len(universe)} 只）"
    )
    for reason, count in rejected_stats.items():
        if count:
            logger.info(f"    · {REASON_LABELS.get(reason, reason)}: {count} 只")
    if missing_market_cap:
        logger.warning(
            f"⚠️  {missing_market_cap} 只缺少总市值（行情缺失，未剔除；"
            f"如需强制剔除请加 --require-market-cap）"
        )

    return {
        "date": date_str,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "rules": rules.to_dict(),
        "universe": universe,
        "rejected": rejected,
        "stats": stats,
        "metrics": {t: m.to_dict() for t, m in metrics_map.items()},
    }


# ─────────────────────────────────────────────────────────────────────────────
# 快照读写 / 供各选股脚本调用
# ─────────────────────────────────────────────────────────────────────────────


def universe_path(date_str: str) -> Path:
    """返回指定日期的股票池快照路径。"""
    from trade_krono_cli.config import get_settings  # noqa: PLC0415

    results_dir = Path(get_settings().results_dir)
    return results_dir / f"{UNIVERSE_FILE_PREFIX}{date_str.replace('-', '')}.json"


def save_universe(snapshot: dict[str, Any], path: Path | None = None) -> Path:
    """把快照写入 ``outputs/results/universe_YYYYMMDD.json``（原子写）。"""
    target = path or universe_path(str(snapshot["date"]))
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(snapshot, ensure_ascii=False), encoding="utf-8")
    tmp.replace(target)
    logger.info(f"💾 股票池快照已写入: {target}")
    return target


def load_universe(
    date_str: str | None = None,
    *,
    max_age_days: int = UNIVERSE_MAX_AGE_DAYS,
) -> dict[str, Any] | None:
    """读取股票池快照（指定日期缺失时回退到最近 ``max_age_days`` 天内的文件）。

    Args:
        date_str: 目标日期（YYYY-MM-DD），缺省为最近交易日。
        max_age_days: 允许回退的最大天数（周末/节假日/手动重放）。

    Returns:
        快照字典；找不到时返回 None。
    """
    date_str = date_str or expected_trading_day()
    exact = universe_path(date_str)
    if exact.exists():
        return _read_snapshot(exact)

    from trade_krono_cli.config import get_settings  # noqa: PLC0415

    results_dir = Path(get_settings().results_dir)
    candidates = sorted(results_dir.glob(f"{UNIVERSE_FILE_PREFIX}*.json"), reverse=True)
    try:
        target_ts = datetime.strptime(date_str, "%Y-%m-%d")
    except ValueError:
        target_ts = datetime.now()
    for path in candidates:
        stamp = path.stem.removeprefix(UNIVERSE_FILE_PREFIX)
        try:
            age = abs((target_ts - datetime.strptime(stamp, "%Y%m%d")).days)
        except ValueError:
            continue
        if age <= max_age_days:
            snapshot = _read_snapshot(path)
            if snapshot is not None:
                logger.debug(f"使用 {path.name} 作为 {date_str} 的前置股票池")
                return snapshot
    return None


def _read_snapshot(path: Path) -> dict[str, Any] | None:
    """读取并校验快照文件（损坏时返回 None）。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning(f"⚠️  股票池快照读取失败（{path}）: {exc}")
        return None
    if not isinstance(data, dict) or not isinstance(data.get("universe"), list):
        logger.warning(f"⚠️  股票池快照格式异常: {path}")
        return None
    return data


def apply_universe_filter(
    tickers: list[str],
    date_str: str | None = None,
    *,
    label: str = "",
    keep_hk: bool = True,
    strict: bool = False,
) -> list[str]:
    """把股票列表与当日前置股票池求交集（供各选股脚本统一调用）。

    快照缺失时默认原样返回（灰度回退），``strict=True`` 时抛 ``FileNotFoundError``。

    Args:
        tickers: 待过滤的 ticker 列表（带前缀或 6 位裸代码均可）。
        date_str: 分析日期，缺省为最近交易日。
        label: 日志标签（脚本名）。
        keep_hk: 是否保留港股（港股无 A 股 ST/停牌口径，白名单分析仍需）。
        strict: 快照缺失时是否报错。

    Returns:
        过滤后的 ticker 列表（保持原顺序）。
    """
    snapshot = load_universe(date_str)
    if snapshot is None:
        message = (
            f"未找到前置股票池快照（{date_str or expected_trading_day()}），"
            f"跳过统一过滤，沿用脚本原有股票池"
        )
        if strict:
            raise FileNotFoundError(message)
        logger.warning(f"⚠️  [{label}] {message}")
        return list(tickers)

    allowed = {t.split(".")[-1] for t in snapshot["universe"]}
    reasons_by_ticker: dict[str, str] = {}
    for reason, codes in (snapshot.get("rejected") or {}).items():
        for code in codes:
            reasons_by_ticker.setdefault(str(code), reason)

    kept: list[str] = []
    dropped: dict[str, int] = {}
    for ticker in tickers:
        key = market_key(ticker)
        if key == "hk" and keep_hk:
            kept.append(ticker)
            continue
        bare = ticker.split(".")[-1]
        if bare in allowed:
            kept.append(ticker)
            continue
        reason = reasons_by_ticker.get(ticker) or reasons_by_ticker.get(bare) or key
        dropped[reason] = dropped.get(reason, 0) + 1

    if dropped:
        detail = "、".join(
            f"{REASON_LABELS.get(r, r)} {n} 只" for r, n in sorted(dropped.items(), key=lambda x: -x[1])
        )
        logger.info(f"🧹 [{label}] 前置股票池过滤：{len(tickers)} → {len(kept)} 只（剔除 {detail}）")
    else:
        logger.info(f"🧹 [{label}] 前置股票池过滤：{len(tickers)} 只全部通过")
    return kept


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="全市场统一前置股票池过滤（A股）")
    parser.add_argument(
        "--date", default=None, help="分析日期 YYYY-MM-DD（默认最近交易日）"
    )
    parser.add_argument(
        "--min-market-cap",
        type=float,
        default=DEFAULT_MIN_MARKET_CAP_YI,
        help=f"最小总市值（亿元，默认 {DEFAULT_MIN_MARKET_CAP_YI:g}）",
    )
    parser.add_argument(
        "--max-stale-days",
        type=int,
        default=MAX_STALE_CALENDAR_DAYS,
        help=f"最新 K 线允许滞后的自然日数（默认 {MAX_STALE_CALENDAR_DAYS}）",
    )
    parser.add_argument(
        "--require-market-cap",
        action="store_true",
        help="市值缺失时直接剔除（默认仅计数、不剔除）",
    )
    parser.add_argument(
        "--no-save", action="store_true", help="只打印结果，不写快照文件"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """命令行入口：构建并保存前置股票池快照。"""
    args = parse_args(argv)
    date_str = args.date or expected_trading_day()
    rules = UniverseRules(
        min_market_cap_yi=args.min_market_cap,
        max_stale_calendar_days=args.max_stale_days,
        require_market_cap=args.require_market_cap,
    )

    logger.info(f"🚦 构建前置股票池，日期={date_str}，市值下限={rules.min_market_cap_yi:g}亿")
    snapshot = build_universe(date_str, rules=rules)
    if not snapshot["universe"]:
        logger.error("❌ 股票池为空，请检查 K 线缓存与行情源")
        return 1
    if not args.no_save:
        save_universe(snapshot)
    return 0


if __name__ == "__main__":
    sys.exit(main())
