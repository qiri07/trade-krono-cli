"""六维选股筛选：逻辑 → 估值 → 位置 → 趋势 → 股东人数 → 量价关系。

按用户指定的优先级设计，权重严格递减，前两维为**硬门槛**（不满足直接淘汰），
后四维参与排序：

| 维度 | 权重 | 内涵 | 作用 |
|------|------|------|------|
| 逻辑 | 30% | 商业模式/护城河（ROE、扣非 ROE、3 年增长、现金流质量、负债率、毛利率） | 门槛：不过关直接淘汰 |
| 估值 | 23% | PE/PB 五年分位、PEG、绝对 PE | 门槛：历史高估或 PEG 过高淘汰 |
| 位置 | 15% | 股价在 3 年区间中的分位 | 排序：偏中低位加分，历史高位扣分 |
| 趋势 | 13% | 均线排列、MA60 斜率、MACD | 门槛 + 排序：明确下跌趋势淘汰 |
| 股东人数 | 10% | 户数环比变化（减少 = 筹码集中） | 排序 |
| 量价关系 | 9% | 成交额放量倍数、涨跌量能配合 | 排序 |

数据来源（全部本地缓存 + 两个公开接口）：
- 基本面：``outputs/cache/buffett_cache.db``（同花顺 Fuyao 缓存：ROE/毛利率/负债率/利润/现金流）
- 估值分位：``akshare.stock_zh_valuation_baidu``（百度股市通，近五年 PE/PB 序列）
- 股东户数：``akshare.stock_zh_a_gdhs``（东财，最近一个披露季度全市场一览）
- 总市值：``akshare.stock_zh_a_spot_em``（东财实时快照，当日磁盘缓存）
- K 线：``outputs/cache/pipeline_cache.db``（本地日线，已剔除 ST/北交所/港股/ETF）

用法::

    uv run python scripts/six_dim_screen.py                     # 默认推荐 10 只
    uv run python scripts/six_dim_screen.py --top 20            # 推荐 20 只
    uv run python scripts/six_dim_screen.py --no-fetch          # 只用本地缓存，跳过联网接口
    uv run python scripts/six_dim_screen.py --min-cap 30        # 最小总市值 30 亿
"""

from __future__ import annotations

import argparse
import io
import json
import math
import sqlite3
import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
from loguru import logger

from scripts._utils import (
    BSE_TICKER_PREFIXES,
    filter_stock_universe,
    get_cache_db_path,
    load_metadata_index,
    normalize_ticker_key,
    ticker_to_thscode,
)

# ── 权重（严格按「逻辑 > 估值 > 位置 > 趋势 > 股东人数 > 量价」递减）──────────
DIM_WEIGHTS: tuple[tuple[str, float], ...] = (
    ("logic", 0.30),
    ("valuation", 0.23),
    ("position", 0.15),
    ("trend", 0.13),
    ("holder", 0.10),
    ("vol_price", 0.09),
)
DIM_LABELS: dict[str, str] = {
    "logic": "逻辑",
    "valuation": "估值",
    "position": "位置",
    "trend": "趋势",
    "holder": "股东人数",
    "vol_price": "量价关系",
}

# ── 门槛常量 ───────────────────────────────────────────────────────────────────
MIN_ROE_AVG = 12.0  # 近两年 ROE 均值下限（%）
MIN_ROE_EXCL_AVG = 10.0  # 近两年扣非 ROE 均值下限（%）
MAX_DEBT_RATIO = 65.0  # 资产负债率上限（%），金融股豁免
MIN_PROFIT_YI = 1.0  # 最近年度归母净利润下限（亿元）
MIN_CFO_RATIO = 0.5  # 经营现金流 / 净利润 下限
MAX_PE = 30.0  # PE_TTM 上限
MAX_PB = 6.0  # PB 上限
MAX_PE_PCT = 60.0  # PE 五年分位上限（%）
MAX_PEG = 2.5  # PEG 上限
MIN_MARKET_CAP_YI = 50.0  # 最小总市值（亿元）
POSITION_WINDOW_DAYS = 750  # 位置分位窗口（约 3 年交易日）

# 金融股识别（毛利率缺失 + 高负债 = 银行/保险/券商，负债率门槛豁免）
FIN_INDUSTRY_HINTS = ("银行", "保险", "证券", "信托", "多元金融")


@dataclass
class LogicMetrics:
    """逻辑维度的原始基本面指标（来自 buffett_cache.db）。"""

    code: str
    name: str
    roe_avg: float
    roe_min: float
    roe_excl_avg: float
    debt_ratio: float | None
    gross_margin: float | None
    profit_cagr: float
    revenue_cagr: float
    cfo_ratio: float | None
    latest_profit_yi: float
    latest_profit_yoy: float
    positive_years: int
    total_years: int
    is_financial: bool = False

    def to_payload(self) -> dict[str, Any]:
        """序列化为可写入 JSON 的字典。"""
        return {
            "code": self.code,
            "name": self.name,
            "roe_avg": round(self.roe_avg, 2),
            "roe_min": round(self.roe_min, 2),
            "roe_excl_avg": round(self.roe_excl_avg, 2),
            "debt_ratio": None if self.debt_ratio is None else round(self.debt_ratio, 1),
            "gross_margin": None if self.gross_margin is None else round(self.gross_margin, 1),
            "profit_cagr": round(self.profit_cagr, 2),
            "revenue_cagr": round(self.revenue_cagr, 2),
            "cfo_ratio": None if self.cfo_ratio is None else round(self.cfo_ratio, 2),
            "latest_profit_yi": round(self.latest_profit_yi, 2),
            "latest_profit_yoy": round(self.latest_profit_yoy, 2),
            "positive_years": self.positive_years,
            "total_years": self.total_years,
            "is_financial": self.is_financial,
        }


@dataclass
class StockScore:
    """单只股票的六维打分结果。"""

    code: str
    name: str
    ticker: str
    logic: float = 0.0
    valuation: float = 0.0
    position: float = 0.0
    trend: float = 0.0
    holder: float = 0.0
    vol_price: float = 0.0
    composite: float = 0.0
    metrics: dict[str, Any] = field(default_factory=dict)
    notes: dict[str, str] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        """序列化为可写入 JSON 的字典。"""
        payload: dict[str, Any] = {
            "code": self.code,
            "name": self.name,
            "ticker": self.ticker,
            "composite": round(self.composite, 2),
        }
        for key, _ in DIM_WEIGHTS:
            payload[key] = round(getattr(self, key), 1)
        payload["metrics"] = self.metrics
        payload["notes"] = self.notes
        return payload

    @property
    def priority_key(self) -> tuple[float, ...]:
        """按优先级字典序排序的键（逻辑 → 估值 → 位置 → 趋势 → 股东 → 量价）。"""
        return (
            self.logic,
            self.valuation,
            self.position,
            self.trend,
            self.holder,
            self.vol_price,
            self.composite,
        )


# ── 工具函数 ───────────────────────────────────────────────────────────────────


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    """把数值裁剪到 [lo, hi] 区间。"""
    return max(lo, min(hi, value))


def _pct_rank(series: Iterable[float], value: float) -> float:
    """计算 value 在序列中的百分位（0~100，越小越便宜）。"""
    vals = [v for v in series if v is not None and v > 0 and math.isfinite(v)]
    if not vals or value is None or value <= 0:
        return 100.0
    below = sum(1 for v in vals if v <= value)
    return below / len(vals) * 100.0


def _recent_quarters(today: date, n: int = 6) -> list[str]:
    """返回最近 n 个已披露季度的季度末日期字符串（YYYYMMDD，降序）。"""
    quarters: list[str] = []
    y, m = today.year, today.month
    for _ in range(n):
        # 季度末：3/6/9/12 月末，披露滞后一个季度，故从上一季度末开始回退
        q_month = ((m - 1) // 3) * 3
        if q_month == 0:
            q_month = 12
            y -= 1
        q_day = 31 if q_month in (3, 12) else 30
        quarters.append(f"{y}{q_month:02d}{q_day}")
        m = q_month - 2
        if m <= 0:
            m += 12
            y -= 1
    return quarters


# ── 逻辑维度：基本面加载与打分 ────────────────────────────────────────────────


def _parse_thscode(key: str, prefix: str) -> str | None:
    """从缓存键中解析同花顺代码，如 ``fin_600004.SH_2025-4`` → ``600004.SH``。"""
    if not key.startswith(prefix):
        return None
    rest = key[len(prefix) :]
    parts = rest.split("_")
    if len(parts) < 1:
        return None
    code = parts[0]
    return code if len(code) == 9 and code[6] == "." else None


def load_fundamentals(buffett_db: Path) -> dict[str, LogicMetrics]:
    """从巴菲特缓存库加载并汇总逻辑维度指标。

    Args:
        buffett_db: ``buffett_cache.db`` 路径。

    Returns:
        同花顺代码（``600519.SH``）→ :class:`LogicMetrics`。
    """
    if not buffett_db.exists():
        logger.warning(f"巴菲特缓存不存在：{buffett_db}")
        return {}

    conn = sqlite3.connect(f"file:{buffett_db}?mode=ro", uri=True)
    try:
        valuations: dict[str, dict[str, Any]] = {}
        for key, data in conn.execute("SELECT key, data FROM cache_valuations"):
            if not key.startswith("val_"):
                continue
            code = key[4:]
            try:
                valuations[code] = json.loads(data)
            except json.JSONDecodeError:
                continue

        financials: dict[str, dict[str, dict[str, Any]]] = {}
        for key, data in conn.execute("SELECT key, data FROM cache_financials"):
            code = _parse_thscode(key, "fin_")
            if not code:
                continue
            period = key.rsplit("_", 1)[-1]
            try:
                financials.setdefault(code, {})[period] = json.loads(data)
            except json.JSONDecodeError:
                continue

        incomes: dict[str, list[dict[str, Any]]] = {}
        for key, data in conn.execute("SELECT key, data FROM cache_income"):
            if not key.startswith("income_"):
                continue
            code = key[len("income_") :]
            try:
                incomes[code] = json.loads(data).get("items", [])
            except json.JSONDecodeError:
                continue

        cfos: dict[str, float] = {}
        for key, data in conn.execute("SELECT key, data FROM cache_cfo"):
            code = key[len("cfo_") :] if key.startswith("cfo_") else ""
            if not code or "_" in code:
                continue
            try:
                cfos[code] = float(json.loads(data)["cfo"])
            except (json.JSONDecodeError, KeyError, TypeError, ValueError):
                continue
    finally:
        conn.close()

    metrics: dict[str, LogicMetrics] = {}
    for code, val in valuations.items():
        fins = financials.get(code) or {}
        latest = fins.get("2025-4")
        prev = fins.get("2024-4")
        if not latest or prev is None or latest.get("roe") is None or prev.get("roe") is None:
            continue
        roe_pairs = [latest["roe"], prev["roe"]]
        excl_pairs = [v for v in (latest.get("roe_excl"), prev.get("roe_excl")) if v is not None]
        roe_avg = sum(roe_pairs) / len(roe_pairs)
        roe_excl_avg = sum(excl_pairs) / len(excl_pairs) if excl_pairs else roe_avg

        items = [i for i in incomes.get(code, []) if i.get("fiscal_year")]
        items.sort(key=lambda x: x["fiscal_year"])
        if len(items) < 4:
            continue
        first, last = items[0], items[-1]
        np0 = float(first.get("parent_holder_net_profit") or 0)
        np1 = float(last.get("parent_holder_net_profit") or 0)
        rev0 = float(first.get("operating_income") or 0)
        rev1 = float(last.get("operating_income") or 0)
        years = max(1, int(last["fiscal_year"]) - int(first["fiscal_year"]))
        if np0 <= 0 or rev0 <= 0:
            continue
        profit_cagr = ((np1 / np0) ** (1 / years) - 1) * 100
        revenue_cagr = ((rev1 / rev0) ** (1 / years) - 1) * 100

        prev_profit = float(items[-2].get("parent_holder_net_profit") or 0)
        profit_yoy = ((np1 / prev_profit) - 1) * 100 if prev_profit > 0 else -100.0
        positive_years = sum(1 for i in items if float(i.get("parent_holder_net_profit") or 0) > 0)

        cfo = cfos.get(code)
        cfo_ratio = (cfo / np1) if cfo and np1 > 0 else None

        debt = latest.get("debt_ratio")
        gross = latest.get("gross_margin")
        is_financial = gross is None and debt is not None and debt > 70

        metrics[code] = LogicMetrics(
            code=code.split(".")[0],
            name=str(val.get("name") or ""),
            roe_avg=roe_avg,
            roe_min=min(roe_pairs),
            roe_excl_avg=roe_excl_avg,
            debt_ratio=float(debt) if debt is not None else None,
            gross_margin=float(gross) if gross is not None else None,
            profit_cagr=profit_cagr,
            revenue_cagr=revenue_cagr,
            cfo_ratio=cfo_ratio,
            latest_profit_yi=np1 / 1e8,
            latest_profit_yoy=profit_yoy,
            positive_years=positive_years,
            total_years=len(items),
            is_financial=is_financial,
        )
    logger.info(f"逻辑维度：从巴菲特缓存加载 {len(metrics)} 只个股基本面")
    return metrics


def score_logic(m: LogicMetrics) -> tuple[bool, float, str]:
    """逻辑维度硬门槛 + 打分。

    Args:
        m: 单只股票的逻辑指标。

    Returns:
        ``(是否通过, 0~100 分, 淘汰原因或通过说明)``。
    """
    if m.roe_avg < MIN_ROE_AVG:
        return False, 0.0, f"ROE均值 {m.roe_avg:.1f}% < {MIN_ROE_AVG}%"
    if m.roe_excl_avg < MIN_ROE_EXCL_AVG:
        return False, 0.0, f"扣非ROE均值 {m.roe_excl_avg:.1f}% < {MIN_ROE_EXCL_AVG}%"
    if m.profit_cagr <= 0:
        return False, 0.0, f"3年净利CAGR {m.profit_cagr:.1f}% ≤ 0"
    if m.cfo_ratio is None or m.cfo_ratio < MIN_CFO_RATIO:
        return False, 0.0, f"现金流/净利 {m.cfo_ratio} < {MIN_CFO_RATIO}"
    if m.latest_profit_yi < MIN_PROFIT_YI:
        return False, 0.0, f"净利润 {m.latest_profit_yi:.2f}亿 < {MIN_PROFIT_YI}亿"
    if m.positive_years < m.total_years:
        return False, 0.0, f"近 {m.total_years} 年有 {m.total_years - m.positive_years} 年亏损"
    if not m.is_financial and m.debt_ratio is not None and m.debt_ratio > MAX_DEBT_RATIO:
        return False, 0.0, f"资产负债率 {m.debt_ratio:.1f}% > {MAX_DEBT_RATIO}%"

    roe_score = _clamp((m.roe_avg - 10) / 20 * 100)  # 10%→0, 30%→100
    excl_score = _clamp(m.roe_excl_avg / max(m.roe_avg, 1e-6) * 100)
    growth_score = _clamp(m.profit_cagr / 25 * 100)
    revenue_score = _clamp((m.revenue_cagr + 5) / 25 * 100)
    cfo_score = _clamp((m.cfo_ratio or 0) / 1.2 * 100)
    stability_score = _clamp(60 + (m.roe_min - m.roe_avg) * 2 + min(m.latest_profit_yoy, 30) * 0.8)
    debt_score = (
        60.0 if m.is_financial else _clamp((MAX_DEBT_RATIO - (m.debt_ratio or 0)) / 45 * 100)
    )
    gross_score = 55.0 if m.gross_margin is None else _clamp((m.gross_margin - 20) / 50 * 100)

    score = (
        roe_score * 0.26
        + excl_score * 0.10
        + growth_score * 0.20
        + revenue_score * 0.06
        + cfo_score * 0.16
        + stability_score * 0.10
        + debt_score * 0.06
        + gross_score * 0.06
    )
    return True, _clamp(score), "逻辑成立"


# ── 估值维度：五年 PE/PB 分位 ─────────────────────────────────────────────────


def fetch_valuation_history(
    codes: list[str], workers: int = 8, retries: int = 2
) -> dict[str, dict[str, float]]:
    """批量拉取五年 PE/PB 序列并计算当前分位。

    Args:
        codes: 6 位股票代码列表。
        workers: 并发线程数。
        retries: 单个指标失败重试次数。

    Returns:
        ``code -> {"pe_pct": float, "pb_pct": float, "pe": float, "pb": float}``。
    """
    import akshare as ak

    def _one(code: str) -> tuple[str, dict[str, float]]:
        result: dict[str, float] = {}
        for indicator, key in (("市盈率(TTM)", "pe"), ("市净率", "pb")):
            for attempt in range(retries + 1):
                try:
                    df = ak.stock_zh_valuation_baidu(
                        symbol=code, indicator=indicator, period="近五年"
                    )
                    if df is None or df.empty:
                        break
                    series = pd.to_numeric(df["value"], errors="coerce").dropna()
                    if series.empty:
                        break
                    latest = float(series.iloc[-1])
                    result[key] = latest
                    result[f"{key}_pct"] = _pct_rank(series.tolist(), latest)
                    break
                except (OSError, ValueError, KeyError, TypeError):
                    if attempt < retries:
                        time.sleep(0.6 * (attempt + 1))
        return code, result

    out: dict[str, dict[str, float]] = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for code, res in ex.map(_one, codes):
            if res:
                out[code] = res
    logger.info(f"估值分位：成功获取 {len(out)}/{len(codes)} 只的五年 PE/PB 序列")
    return out


def score_valuation(
    pe: float | None,
    pb: float | None,
    hist: dict[str, float] | None,
    profit_cagr: float,
) -> tuple[bool, float, str]:
    """估值维度硬门槛 + 打分（PE/PB 绝对 + 五年分位 + PEG）。

    Args:
        pe: PE_TTM。
        pb: PB。
        hist: :func:`fetch_valuation_history` 返回的单只结果（可缺）。
        profit_cagr: 3 年净利 CAGR（%），用于 PEG。

    Returns:
        ``(是否通过, 0~100 分, 说明)``。
    """
    if pe is None or not (0 < pe < MAX_PE):
        return False, 0.0, f"PE={pe} 不在 (0, {MAX_PE})"
    if pb is None or not (0 < pb < MAX_PB):
        return False, 0.0, f"PB={pb} 不在 (0, {MAX_PB})"
    pe_pct = hist.get("pe_pct") if hist else None
    pb_pct = hist.get("pb_pct") if hist else None
    peg = pe / profit_cagr if profit_cagr > 0 else float("inf")
    if pe_pct is not None and pe_pct > MAX_PE_PCT:
        return False, 0.0, f"PE五年分位 {pe_pct:.0f}% > {MAX_PE_PCT:.0f}%"
    if peg > MAX_PEG:
        return False, 0.0, f"PEG {peg:.2f} > {MAX_PEG}"

    abs_score = _clamp((MAX_PE - pe) / MAX_PE * 100)
    pe_pct_score = 50.0 if pe_pct is None else 100 - pe_pct
    pb_pct_score = 50.0 if pb_pct is None else 100 - pb_pct
    peg_score = _clamp((MAX_PEG - peg) / MAX_PEG * 100)
    score = abs_score * 0.20 + pe_pct_score * 0.35 + pb_pct_score * 0.20 + peg_score * 0.25
    detail = f"PE={pe:.1f}(分位{pe_pct:.0f}%)" if pe_pct is not None else f"PE={pe:.1f}"
    detail += f" PB={pb:.2f} PEG={peg:.2f}"
    return True, _clamp(score), detail


# ── 技术面：K 线加载（位置 / 趋势 / 量价）─────────────────────────────────────


def _compute_technicals(df: pd.DataFrame) -> dict[str, float] | None:
    """从日线 DataFrame 计算位置 / 趋势 / 量价指标。

    Args:
        df: 含 ``timestamps/open/high/low/close/volume/amount`` 的日线数据。

    Returns:
        指标字典；数据不足 130 根时返回 ``None``。
    """
    if df is None or len(df) < 130:
        return None
    data = df.sort_values("timestamps").reset_index(drop=True)
    close = pd.to_numeric(data["close"], errors="coerce")
    amount = pd.to_numeric(data["amount"], errors="coerce")
    if close.isna().all() or amount.isna().all():
        return None

    ma = {n: close.rolling(n).mean() for n in (5, 10, 20, 60, 120)}
    last_close = float(close.iloc[-1])

    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()

    window = min(POSITION_WINDOW_DAYS, len(close))
    win_close = close.iloc[-window:]
    low_n, high_n = float(win_close.min()), float(win_close.max())
    pct_pos = (last_close - low_n) / (high_n - low_n) * 100 if high_n > low_n else 50.0

    win1y = close.iloc[-250:] if len(close) >= 250 else close
    low1y, high1y = float(win1y.min()), float(win1y.max())
    pct_pos_1y = (last_close - low1y) / (high1y - low1y) * 100 if high1y > low1y else 50.0

    ma60_slope = (
        (ma[60].iloc[-1] / ma[60].iloc[-21] - 1) * 100 if len(ma[60].dropna()) > 21 else 0.0
    )
    ma20_slope = (
        (ma[20].iloc[-1] / ma[20].iloc[-11] - 1) * 100 if len(ma[20].dropna()) > 11 else 0.0
    )

    amt5 = float(amount.iloc[-5:].mean())
    amt60 = float(amount.iloc[-60:].mean())
    amt_ratio = amt5 / amt60 if amt60 > 0 else 1.0

    last20 = data.iloc[-20:]
    up_mask = last20["close"] >= last20["open"]
    up_amt = float(last20.loc[up_mask, "amount"].sum())
    down_amt = float(last20.loc[~up_mask, "amount"].sum())
    updown_ratio = up_amt / down_amt if down_amt > 0 else 2.0

    ret5 = (last_close / float(close.iloc[-6]) - 1) * 100 if len(close) > 6 else 0.0
    ret20 = (last_close / float(close.iloc[-21]) - 1) * 100 if len(close) > 21 else 0.0

    return {
        "close": last_close,
        "ma5": float(ma[5].iloc[-1]),
        "ma10": float(ma[10].iloc[-1]),
        "ma20": float(ma[20].iloc[-1]),
        "ma60": float(ma[60].iloc[-1]),
        "ma120": float(ma[120].iloc[-1]),
        "ma20_slope": ma20_slope,
        "ma60_slope": ma60_slope,
        "dif": float(dif.iloc[-1]),
        "dea": float(dea.iloc[-1]),
        "pct_pos_3y": max(0.0, min(100.0, pct_pos)),
        "pct_pos_1y": max(0.0, min(100.0, pct_pos_1y)),
        "high_3y": high_n,
        "low_3y": low_n,
        "amt_ratio": amt_ratio,
        "updown_amt_ratio": updown_ratio,
        "ret5": ret5,
        "ret20": ret20,
        "bars": float(len(data)),
        "last_bar_ts": float(pd.to_datetime(data["timestamps"].iloc[-1]).timestamp()),
    }


def load_technicals(
    tickers: dict[str, str], db_path: Path, workers: int = 8
) -> dict[str, dict[str, float]]:
    """并发从 K 线缓存读取并计算技术指标。

    Args:
        tickers: ``code -> 带前缀 ticker`` 映射。
        db_path: ``pipeline_cache.db`` 路径。
        workers: 并发线程数。

    Returns:
        ``code -> 技术指标字典``；读取失败或数据不足的股票不会出现在结果中。
    """

    def _one(item: tuple[str, str]) -> tuple[str, dict[str, float] | None]:
        code, ticker = item
        try:
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=30)
            try:
                row = conn.execute(
                    "SELECT data FROM kline_cache WHERE ticker=? AND freq='d' "
                    "ORDER BY end DESC LIMIT 1",
                    (ticker,),
                ).fetchone()
            finally:
                conn.close()
            if not row:
                return code, None
            df = pd.read_pickle(io.BytesIO(row[0]))
            return code, _compute_technicals(df)
        except (sqlite3.Error, OSError, ValueError, KeyError):
            return code, None

    out: dict[str, dict[str, float]] = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for code, res in ex.map(_one, tickers.items()):
            if res is not None:
                out[code] = res
    logger.info(f"技术面：成功计算 {len(out)}/{len(tickers)} 只的位置/趋势/量价指标")
    return out


def score_position(tech: dict[str, float]) -> tuple[float, str]:
    """位置维度打分：3 年分位越接近 35% 越好，历史高位扣分。

    Args:
        tech: :func:`_compute_technicals` 返回的指标。

    Returns:
        ``(0~100 分, 说明)``。
    """
    pct = tech["pct_pos_3y"]
    score = _clamp(100 - abs(pct - 35) * 1.5)
    if pct > 85:
        score = _clamp(score - 15)  # 历史高位，风险收益比恶化
    band = "低位" if pct < 25 else "中低位" if pct < 50 else "中高位" if pct < 75 else "高位"
    detail = f"3年分位 {pct:.0f}%（{band}），距3年高点 {(1 - tech['close'] / tech['high_3y']) * 100:.0f}%"
    return score, detail


def trend_gate_and_score(tech: dict[str, float]) -> tuple[bool, float, str]:
    """趋势维度：下跌趋势硬淘汰 + 均线/MACD 打分。

    Args:
        tech: 技术指标。

    Returns:
        ``(是否通过, 0~100 分, 说明)``。
    """
    close = tech["close"]
    ma20, ma60, ma120 = tech["ma20"], tech["ma60"], tech["ma120"]
    if close < ma60 and close < ma120 and tech["ma60_slope"] < 0:
        return False, 0.0, "价格位于 MA60/MA120 下方且 MA60 向下（下跌趋势）"

    align = 0.0
    align += 25 if close > ma20 else 0
    align += 25 if ma20 > ma60 else 0
    align += 25 if ma60 > ma120 else 0
    align += 25 if close > tech["ma5"] > tech["ma10"] else 0
    slope_score = _clamp(50 + tech["ma60_slope"] * 6)
    macd_score = 0.0
    macd_score += 45 if tech["dif"] > 0 else 0
    macd_score += 35 if tech["dif"] > tech["dea"] else 0
    macd_score += 20 if tech["ret20"] > 0 else 0
    score = (
        align * 0.35
        + slope_score * 0.25
        + macd_score * 0.25
        + (100 if close > ma120 else 40) * 0.15
    )
    trend_txt = (
        "多头排列" if align >= 75 else "偏多" if align >= 50 else "震荡" if align >= 25 else "偏弱"
    )
    detail = (
        f"{trend_txt}，MA60 20日斜率 {tech['ma60_slope']:+.1f}%，"
        f"MACD DIF {tech['dif']:+.2f}，20日涨幅 {tech['ret20']:+.1f}%"
    )
    return True, _clamp(score), detail


def score_vol_price(tech: dict[str, float]) -> tuple[float, str]:
    """量价关系打分：温和放量 + 涨时量大于跌时量 + 量价同向。

    Args:
        tech: 技术指标。

    Returns:
        ``(0~100 分, 说明)``。
    """
    ratio = tech["amt_ratio"]
    ratio_score = _clamp(100 - abs(ratio - 1.35) * 55)
    if ratio > 3:
        ratio_score *= 0.6  # 异常放量，警惕出货
    flow_score = _clamp((tech["updown_amt_ratio"] - 0.6) / 0.9 * 100)
    consistency = 80.0
    if tech["ret5"] > 0 and ratio < 0.75:
        consistency = 45.0  # 缩量上涨，突破力度不足
    elif tech["ret5"] < -5 and ratio > 1.2:
        consistency = 40.0  # 放量下跌，短期资金流出
    elif tech["ret5"] < -3 and ratio > 1.8:
        consistency = 25.0  # 放量下跌，危险信号
    elif tech["ret5"] > 3 and ratio > 1.2:
        consistency = 95.0
    score = ratio_score * 0.40 + flow_score * 0.40 + consistency * 0.20
    detail = (
        f"5日/60日成交额 {ratio:.2f}×，20日涨跌量比 {tech['updown_amt_ratio']:.2f}，"
        f"近5日 {tech['ret5']:+.1f}%"
    )
    return _clamp(score), detail


# ── 股东人数维度 ─────────────────────────────────────────────────────────────


def fetch_holder_counts(today: date | None = None) -> dict[str, dict[str, float]]:
    """拉取最近一期全市场股东户数一览。

    Args:
        today: 基准日期；缺省用系统当天。

    Returns:
        ``code -> {"chg_pct": 环比变化(%), "holders": 本次户数, "avg_value": 户均市值}``。
    """
    import akshare as ak

    today = today or date.today()
    for quarter in _recent_quarters(today):
        try:
            df = ak.stock_zh_a_gdhs(symbol=quarter)
            if df is None or df.empty:
                continue
        except (OSError, ValueError, KeyError, TypeError) as exc:
            logger.warning(f"股东户数 {quarter} 获取失败：{exc}")
            continue
        out: dict[str, dict[str, float]] = {}
        for _, row in df.iterrows():
            code = str(row.get("代码", "")).zfill(6)
            chg = row.get("股东户数-增减比例")
            try:
                out[code] = {
                    "chg_pct": float(chg) if chg is not None and not pd.isna(chg) else 0.0,
                    "holders": float(row.get("股东户数-本次") or 0),
                    "avg_value": float(row.get("户均持股市值") or 0),
                }
            except (TypeError, ValueError):
                continue
        logger.info(f"股东户数：{quarter} 期共 {len(out)} 只")
        return out
    logger.warning("股东户数：最近 6 个季度均未取到数据，该维度按中性 50 分处理")
    return {}


def score_holder(info: dict[str, float] | None) -> tuple[float, str]:
    """股东人数打分：户数减少（筹码集中）加分，户数增加扣分。

    Args:
        info: :func:`fetch_holder_counts` 返回的单只信息。

    Returns:
        ``(0~100 分, 说明)``。
    """
    if not info:
        return 50.0, "无股东户数数据（中性 50 分）"
    chg = info["chg_pct"]
    score = _clamp(50 - chg * 2.5)
    trend = (
        "明显集中"
        if chg < -10
        else "小幅集中"
        if chg < -3
        else ("基本持平" if chg <= 3 else "有所分散")
    )
    return score, f"股东户数环比 {chg:+.1f}%（{trend}），户均市值 {info['avg_value'] / 1e4:.1f} 万"


# ── 总市值（东财快照，当日磁盘缓存）─────────────────────────────────────────


def fetch_spot_snapshot(cache_dir: Path, date_str: str) -> dict[str, dict[str, float]]:
    """获取东财全市场快照（总市值/流通市值/量比/换手率），按日缓存。

    Args:
        cache_dir: 缓存目录（``outputs/cache``）。
        date_str: 日期字符串（YYYY-MM-DD），用于缓存文件名与 TTL。

    Returns:
        ``code -> {"market_cap_yi": 总市值(亿), "float_cap_yi": 流通市值(亿), ...}``。
    """
    cache_file = cache_dir / f"spot_snapshot_{date_str.replace('-', '')}.json"
    if cache_file.exists():
        try:
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            if isinstance(cached, dict) and cached:
                logger.info(f"总市值：读取当日快照缓存 {cache_file.name}（{len(cached)} 只）")
                return cached
        except (OSError, json.JSONDecodeError):
            logger.warning(f"快照缓存解析失败，重新拉取：{cache_file}")

    import akshare as ak

    try:
        df = ak.stock_zh_a_spot_em()
    except (OSError, ValueError, KeyError, TypeError) as exc:
        logger.warning(f"东财快照获取失败：{exc}，市值过滤将跳过")
        return {}
    out: dict[str, dict[str, float]] = {}
    for _, row in df.iterrows():
        code = str(row.get("代码", "")).zfill(6)
        cap = row.get("总市值")
        if cap is None or pd.isna(cap):
            continue
        out[code] = {
            "market_cap_yi": float(cap) / 1e8,
            "float_cap_yi": float(row.get("流通市值") or 0) / 1e8,
            "turnover": float(row.get("换手率") or 0),
            "volume_ratio": float(row.get("量比") or 0),
        }
    try:
        cache_file.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    except OSError as exc:
        logger.warning(f"快照缓存写入失败：{exc}")
    logger.info(f"总市值：东财快照获取 {len(out)} 只")
    return out


# ── 主流程 ───────────────────────────────────────────────────────────────────


def _load_universe(meta: dict[str, dict[str, object]]) -> dict[str, str]:
    """构建候选股票池：本地 K 线缓存中全部 A 股（剔除 ST/北交所/港股/ETF）。

    Returns:
        ``code -> 带前缀 ticker``。
    """
    db = get_cache_db_path()
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        tickers = [
            r[0] for r in conn.execute("SELECT DISTINCT ticker FROM kline_cache WHERE freq='d'")
        ]
    finally:
        conn.close()
    tickers = [t for t in tickers if not t.startswith(BSE_TICKER_PREFIXES)]
    filtered = filter_stock_universe(tickers, meta, exclude_etf=True)
    logger.info(
        f"股票池：K 线缓存 {len(tickers)} 只 → 过滤后 {len(filtered)} 只（去 ST/北交所/港股/ETF）"
    )
    return {t.split(".")[-1]: t for t in filtered}


def screen(
    *,
    date_str: str | None = None,
    min_cap: float = MIN_MARKET_CAP_YI,
    fetch: bool = True,
    max_candidates: int = 800,
) -> tuple[list[StockScore], dict[str, int]]:
    """执行六维筛选与排序。

    Args:
        date_str: 分析日期（YYYY-MM-DD），缺省为今天。
        min_cap: 最小总市值（亿元）。
        fetch: 是否联网拉取估值分位/股东户数/快照（False 时仅用本地缓存）。
        max_candidates: 进入技术面计算的最大候选数（按逻辑分排序截断）。

    Returns:
        ``(按综合分排序的候选列表, 各关卡人数统计)``；列表包含全部通过门槛的股票，
        由调用方按 ``--top`` 截断。
    """
    date_str = date_str or date.today().isoformat()
    cache_dir = get_cache_db_path().parent
    buffett_db = cache_dir / "buffett_cache.db"
    meta = load_metadata_index(cache_dir)
    universe = _load_universe(meta)

    fundamentals = load_fundamentals(buffett_db)
    spot = fetch_spot_snapshot(cache_dir, date_str) if fetch else {}

    # ── 第 1 关：逻辑 ──
    passed: list[tuple[LogicMetrics, float]] = []
    reject_logic: dict[str, int] = {}
    for code, ticker in universe.items():
        ths = ticker_to_thscode(ticker)
        m = fundamentals.get(ths)
        if m is None:
            reject_logic["无基本面数据"] = reject_logic.get("无基本面数据", 0) + 1
            continue
        cap = spot.get(code, {}).get("market_cap_yi")
        if min_cap and cap is not None and cap < min_cap:
            reject_logic["市值不足"] = reject_logic.get("市值不足", 0) + 1
            continue
        ok, score, reason = score_logic(m)
        if not ok:
            key = reason.split(" ")[0]
            reject_logic[key] = reject_logic.get(key, 0) + 1
            continue
        passed.append((m, score))
    logger.info(f"第 1 关（逻辑）：通过 {len(passed)} 只，淘汰 {sum(reject_logic.values())} 只")
    for reason, n in sorted(reject_logic.items(), key=lambda x: -x[1])[:6]:
        logger.info(f"    · {reason}: {n} 只")

    # ── 第 2 关：估值（绝对 PE/PB 先筛，再查五年分位）──
    reject_abs: dict[str, int] = {}
    reject_val: dict[str, int] = {}
    pe_pb = _load_pe_pb(buffett_db)
    abs_pass: list[tuple[LogicMetrics, float, float, float]] = []
    for m, logic_score in passed:
        pe, pb = pe_pb.get(m.code, (None, None))
        if pe is None or not (0 < pe < MAX_PE) or pb is None or not (0 < pb < MAX_PB):
            reject_abs["PE/PB 缺失或越界"] = reject_abs.get("PE/PB 缺失或越界", 0) + 1
            continue
        abs_pass.append((m, logic_score, pe, pb))
    logger.info(
        f"第 2 关（绝对估值 PE∈(0,{MAX_PE:.0f})、PB∈(0,{MAX_PB:.0f}))：通过 {len(abs_pass)} 只"
    )
    logger.info(f"    · 淘汰 {sum(reject_abs.values())} 只（PE/PB 缺失或越界）")

    # 五年分位（联网）
    abs_pass.sort(key=lambda x: -x[1])
    abs_pass = abs_pass[:max_candidates]
    hist: dict[str, dict[str, float]] = {}
    if fetch and abs_pass:
        hist = fetch_valuation_history([x[0].code for x in abs_pass])

    val_pass: list[
        tuple[LogicMetrics, float, float, float, dict[str, float] | None, float, str]
    ] = []
    for m, logic_score, pe, pb in abs_pass:
        h = hist.get(m.code)
        ok, v_score, detail = score_valuation(pe, pb, h, m.profit_cagr)
        if not ok:
            key = detail.split(" ")[0]
            reject_val[key] = reject_val.get(key, 0) + 1
            continue
        val_pass.append((m, logic_score, pe, pb, h, v_score, detail))
    logger.info(f"第 2 关（估值分位/PEG）：通过 {len(val_pass)} 只")
    for reason, n in sorted(reject_val.items(), key=lambda x: -x[1])[:6]:
        logger.info(f"    · {reason}: {n} 只")

    if not val_pass:
        logger.warning("无股票通过逻辑+估值门槛")
        return [], {
            "universe": len(universe),
            "logic_pass": len(passed),
            "abs_val_pass": 0,
            "val_pass": 0,
            "trend_pass": 0,
        }

    # ── 技术面（位置/趋势/量价）──
    tickers = {x[0].code: normalize_ticker_key(x[0].code) for x in val_pass}
    tech_map = load_technicals(tickers, get_cache_db_path())
    latest_kline_date = (
        date.fromtimestamp(max(t["last_bar_ts"] for t in tech_map.values())).isoformat()
        if tech_map
        else date_str
    )

    # ── 股东户数 ──
    holders = fetch_holder_counts() if fetch else {}

    results: list[StockScore] = []
    for m, logic_score, pe, pb, h, v_score, v_detail in val_pass:
        tech = tech_map.get(m.code)
        if tech is None:
            continue
        ok, t_score, t_detail = trend_gate_and_score(tech)
        if not ok:
            continue
        pos_score, pos_detail = score_position(tech)
        vol_score, vol_detail = score_vol_price(tech)
        h_score, h_detail = score_holder(holders.get(m.code))

        item = StockScore(
            code=m.code,
            name=m.name,
            ticker=normalize_ticker_key(m.code),
            logic=logic_score,
            valuation=v_score,
            position=pos_score,
            trend=t_score,
            holder=h_score,
            vol_price=vol_score,
        )
        item.composite = sum(getattr(item, k) * w for k, w in DIM_WEIGHTS)
        item.metrics = {
            "pe": round(pe, 2),
            "pb": round(pb, 2),
            "pe_pct": None if not h or "pe_pct" not in h else round(h["pe_pct"], 1),
            "pb_pct": None if not h or "pb_pct" not in h else round(h["pb_pct"], 1),
            "peg": round(pe / m.profit_cagr, 2) if m.profit_cagr > 0 else None,
            "roe_avg": round(m.roe_avg, 1),
            "roe_excl_avg": round(m.roe_excl_avg, 1),
            "profit_cagr": round(m.profit_cagr, 1),
            "revenue_cagr": round(m.revenue_cagr, 1),
            "cfo_ratio": None if m.cfo_ratio is None else round(m.cfo_ratio, 2),
            "debt_ratio": None if m.debt_ratio is None else round(m.debt_ratio, 1),
            "gross_margin": None if m.gross_margin is None else round(m.gross_margin, 1),
            "market_cap_yi": round(spot.get(m.code, {}).get("market_cap_yi", 0.0), 1),
            "pct_pos_3y": round(tech["pct_pos_3y"], 1),
            "pct_pos_1y": round(tech["pct_pos_1y"], 1),
            "ma60_slope": round(tech["ma60_slope"], 2),
            "amount_ratio": round(tech["amt_ratio"], 2),
            "ret20": round(tech["ret20"], 2),
            "holder_chg_pct": None if m.code not in holders else holders[m.code]["chg_pct"],
            "close": round(tech["close"], 2),
            "last_date": latest_kline_date,
        }
        logic_detail = (
            f"ROE均值 {m.roe_avg:.1f}%（扣非 {m.roe_excl_avg:.1f}%），"
            f"3年净利CAGR {m.profit_cagr:.1f}%，现金流/净利 {m.cfo_ratio:.2f}"
        )
        item.notes = {
            "logic": logic_detail,
            "valuation": v_detail,
            "position": pos_detail,
            "trend": t_detail,
            "holder": h_detail,
            "vol_price": vol_detail,
        }
        results.append(item)

    logger.info(f"技术面通过（未处于下跌趋势）：{len(results)} 只")
    results.sort(key=lambda s: (s.composite, s.priority_key), reverse=True)
    funnel = {
        "universe": len(universe),
        "logic_pass": len(passed),
        "abs_val_pass": len(abs_pass),
        "val_pass": len(val_pass),
        "trend_pass": len(results),
    }
    return results, funnel


def _load_pe_pb(buffett_db: Path) -> dict[str, tuple[float | None, float | None]]:
    """从巴菲特缓存加载 PE_TTM / PB_MRQ。

    Args:
        buffett_db: ``buffett_cache.db`` 路径。

    Returns:
        ``6 位代码 -> (PE, PB)``。
    """
    if not buffett_db.exists():
        return {}
    conn = sqlite3.connect(f"file:{buffett_db}?mode=ro", uri=True)
    try:
        rows = conn.execute("SELECT key, data FROM cache_valuations").fetchall()
    finally:
        conn.close()
    out: dict[str, tuple[float | None, float | None]] = {}
    for key, data in rows:
        if not key.startswith("val_"):
            continue
        code = key[4:].split(".")[0]
        try:
            v = json.loads(data)
        except json.JSONDecodeError:
            continue
        pe = v.get("pe_ttm")
        pb = v.get("pb_mrq")
        out[code] = (
            float(pe) if isinstance(pe, (int, float)) else None,
            float(pb) if isinstance(pb, (int, float)) else None,
        )
    return out


def build_feishu_card(
    results: list[StockScore],
    date_str: str,
    funnel: dict[str, int],
    rest: list[StockScore] | None = None,
) -> str:
    """构建六维选股飞书汇总卡片。

    top 10 只展开完整明细（含各维度分 + 核心指标）；其余候选只列出
    股票名称与代码，不展开明细。

    Args:
        results: 排序后的推荐列表（前 10 只，含完整明细）。
        date_str: 日期字符串（YYYY-MM-DD）。
        funnel: :func:`screen` 返回的各关卡人数统计。
        rest: 其余候选（未进入 top 10 的筛选中），只列名称 + 代码。

    Returns:
        飞书文本卡片正文。
    """
    lines: list[str] = []
    lines.append(f"📊 **{date_str} 六维选股**")
    lines.append("优先级：逻辑 > 估值 > 位置 > 趋势 > 股东人数 > 量价")
    lines.append("")
    if funnel:
        lines.append(
            f"候选漏斗：A股 {funnel.get('universe', 0)} → "
            f"逻辑 {funnel.get('logic_pass', 0)} → "
            f"绝对估值 {funnel.get('abs_val_pass', 0)} → "
            f"分位/PEG {funnel.get('val_pass', 0)} → "
            f"趋势 {funnel.get('trend_pass', 0)} → 精选 {len(results)}"
        )
        lines.append("")
    for i, r in enumerate(results, 1):
        m = r.metrics
        lines.append(f"{i}. {r.name}（{r.code}）-{r.composite:.1f}分")
        lines.append(
            f"   逻辑{r.logic:.0f} 估值{r.valuation:.0f} 位置{r.position:.0f} "
            f"趋势{r.trend:.0f} 股东{r.holder:.0f} 量价{r.vol_price:.0f}"
        )
        pe_pct = m.get("pe_pct")
        pe_txt = (
            f"PE{m.get('pe')}({pe_pct:.0f}%分位)"
            if isinstance(pe_pct, (int, float))
            else f"PE{m.get('pe')}"
        )
        lines.append(
            f"   {pe_txt} PB{m.get('pb')} ROE{m.get('roe_avg')}% "
            f"净利CAGR{m.get('profit_cagr')}% 市值{m.get('market_cap_yi')}亿"
        )
        pos = m.get("pct_pos_3y")
        slope = m.get("ma60_slope")
        amount_ratio = m.get("amount_ratio")
        holder = m.get("holder_chg_pct")
        pos_txt = f"{pos}" if isinstance(pos, (int, float)) else "-"
        slope_txt = f"{slope:+.1f}" if isinstance(slope, (int, float)) else "-"
        ratio_txt = f"{amount_ratio}" if isinstance(amount_ratio, (int, float)) else "-"
        holder_txt = (
            f"股东户数{holder:+.1f}%" if isinstance(holder, (int, float)) else "股东户数缺失"
        )
        lines.append(
            f"   位置3年{pos_txt}% | MA60斜率{slope_txt}% | 成交额{ratio_txt}× | {holder_txt}"
        )
        lines.append("")
    if rest:
        lines.append("🆕 **其余候选（仅列出名称，不展开明细）**")
        lines.append("")
        for i, r in enumerate(rest, start=len(results) + 1):
            lines.append(f"{i}. {r.name}（{r.code}）")
        lines.append("")
    lines.append("⚠️ 量化筛选结果，不含个股深度尽调，不构成投资建议。")
    return "\n".join(lines)


def write_report(results: list[StockScore], date_str: str, out_dir: Path) -> tuple[Path, Path]:
    """把结果写出 JSON + Markdown 报告。

    Args:
        results: 排序后的打分结果。
        date_str: 日期字符串（YYYY-MM-DD）。
        out_dir: 输出目录（``outputs/results``）。

    Returns:
        ``(json 路径, markdown 路径)``。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    dc = date_str.replace("-", "")
    payload = {
        "date": date_str,
        "weights": dict(DIM_WEIGHTS),
        "gates": {
            "min_roe_avg": MIN_ROE_AVG,
            "min_roe_excl_avg": MIN_ROE_EXCL_AVG,
            "max_debt_ratio": MAX_DEBT_RATIO,
            "min_profit_yi": MIN_PROFIT_YI,
            "min_cfo_ratio": MIN_CFO_RATIO,
            "max_pe": MAX_PE,
            "max_pb": MAX_PB,
            "max_pe_pct": MAX_PE_PCT,
            "max_peg": MAX_PEG,
            "min_market_cap_yi": MIN_MARKET_CAP_YI,
        },
        "count": len(results),
        "results": [r.to_payload() for r in results],
    }
    json_path = out_dir / f"six_dim_screen_{dc}.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        f"# 六维选股结果 — {date_str}",
        "",
        "优先级：逻辑(30%) > 估值(23%) > 位置(15%) > 趋势(13%) > 股东人数(10%) > 量价(9%)",
        "",
        "| # | 股票 | 综合 | 逻辑 | 估值 | 位置 | 趋势 | 股东 | 量价 | PE | PE分位 | ROE | 净利CAGR |",
        "|---|------|------|------|------|------|------|------|------|----|--------|-----|----------|",
    ]
    for i, r in enumerate(results, 1):
        m = r.metrics
        lines.append(
            f"| {i} | {r.name}（{r.code}） | {r.composite:.1f} | {r.logic:.1f} | "
            f"{r.valuation:.1f} | {r.position:.1f} | {r.trend:.1f} | {r.holder:.1f} | "
            f"{r.vol_price:.1f} | {m.get('pe')} | {m.get('pe_pct')}% | "
            f"{m.get('roe_avg')}% | {m.get('profit_cagr')}% |"
        )
    lines.append("")
    for i, r in enumerate(results, 1):
        lines.append(f"## {i}. {r.name}（{r.code}） 综合 {r.composite:.1f}")
        for key, label in DIM_LABELS.items():
            score = getattr(r, key)
            note = r.notes.get(key, "")
            lines.append(f"- **{label} {score:.1f}**：{note}")
        lines.append("")
    md_path = out_dir / f"six_dim_screen_{dc}.md"
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path, md_path


def main(argv: list[str] | None = None) -> int:
    """CLI 入口。"""
    parser = argparse.ArgumentParser(description="六维选股：逻辑>估值>位置>趋势>股东人数>量价")
    parser.add_argument("--top", type=int, default=10, help="推荐股票数量（默认 10）")
    parser.add_argument("--date", default=date.today().isoformat(), help="分析日期 YYYY-MM-DD")
    parser.add_argument(
        "--min-cap", type=float, default=MIN_MARKET_CAP_YI, help="最小总市值（亿元）"
    )
    parser.add_argument("--no-fetch", action="store_true", help="跳过联网接口（仅本地缓存）")
    parser.add_argument(
        "--max-candidates", type=int, default=800, help="进入技术面计算的最大候选数"
    )
    parser.add_argument("--push", type=int, default=0, help="是否飞书推送（1=是，0=否，默认 0）")
    args = parser.parse_args(argv)

    t0 = time.time()
    ranked, funnel = screen(
        date_str=args.date,
        min_cap=args.min_cap,
        fetch=not args.no_fetch,
        max_candidates=args.max_candidates,
    )
    if not ranked:
        logger.error("未筛出符合条件的股票")
        return 1
    results = ranked[: args.top] if args.top > 0 else ranked

    json_path, md_path = write_report(results, args.date, Path("outputs/results"))
    logger.info(f"结果已写入 {json_path} 与 {md_path}（耗时 {time.time() - t0:.0f}s）")

    if args.push:
        from scripts.feishu_utils import push_feishu

        # top 10 展开完整明细；其余候选（排行第 11 名起）只列名称（+代码），不展开明细
        push_pool = ranked
        detailed_results = push_pool[:10] if push_pool else []
        rest_results = [] if not push_pool or len(push_pool) <= 10 else push_pool[10:]
        card = build_feishu_card(detailed_results, args.date, funnel, rest=rest_results)
        ok = push_feishu("text", content=card, title=f"📊 {args.date} 六维选股")
        logger.info(f"{'✅' if ok else '❌'} 飞书推送{'成功' if ok else '失败'}")
        if rest_results:
            logger.info(
                f"📤 飞书卡片：top {len(detailed_results)} 只完整明细 + "
                f"{len(rest_results)} 只候选仅列名称"
            )
    header = "排名  代码    名称        综合  逻辑  估值  位置  趋势  股东  量价"
    logger.info("=" * 84)
    logger.info(header)
    logger.info("-" * 84)
    for i, r in enumerate(results, 1):
        logger.info(
            f"{i:>4}  {r.code}  {r.name:<8}  {r.composite:5.1f} {r.logic:5.1f} "
            f"{r.valuation:5.1f} {r.position:5.1f} {r.trend:5.1f} {r.holder:5.1f} {r.vol_price:5.1f}"
        )
    logger.info("=" * 84)
    for i, r in enumerate(results, 1):
        logger.info(f"#{i} {r.name}（{r.code}）")
        for key, label in DIM_LABELS.items():
            logger.info(f"    {label:<5} {getattr(r, key):5.1f}  {r.notes.get(key, '')}")
    # top 10 之外的候选只列名称（+代码），不展开明细
    rest_display = [] if len(results) <= 10 else results[10:]
    for r in rest_display:
        logger.info(f"  🆕 其余候选: {r.name}（{r.code}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
