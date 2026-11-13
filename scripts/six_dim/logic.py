"""six_dim 选股 — 逻辑维度。

负责从 buffett_cache.db 加载基本面指标，并执行逻辑维度的硬门槛 + 打分。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from loguru import logger

from scripts.six_dim.constants import (
    MAX_DEBT_RATIO,
    MIN_CFO_RATIO,
    MIN_PROFIT_YI,
    MIN_ROE_AVG,
    MIN_ROE_EXCL_AVG,
)
from scripts.six_dim.models import LogicMetrics


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


def _analysis_year(date_str: str | None) -> int:
    """返回分析日期对应的最近已披露年报年份。

    年报在次年 5 月前披露：11 月看 Y-1 年报，3 月看 Y-2 年报。
    避免硬编码年份导致跨年后静默产出 0 只。
    """
    from scripts.six_dim.utils import _analysis_year as _fn

    return _fn(date_str)


def load_fundamentals(
    buffett_db: Path, date_str: str | None = None
) -> dict[str, LogicMetrics]:
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
        fin_years = []
        for y in range(_analysis_year(date_str) - 1, _analysis_year(date_str) - 7, -1):
            if fins.get(f"{y}-4") and fins.get(f"{y - 1}-4"):
                fin_years = [y, y - 1]
                break
        if not fin_years:
            continue
        latest = fins.get(f"{fin_years[0]}-4")
        prev = fins.get(f"{fin_years[1]}-4")
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
    if not m.is_financial and m.debt_ratio is not None and m.debt_ratio > MAX_DEBT_RATIO:
        return False, 0.0, f"资产负债率 {m.debt_ratio:.1f}% > {MAX_DEBT_RATIO}%"
    if m.latest_profit_yi < MIN_PROFIT_YI:
        return False, 0.0, f"净利润 {m.latest_profit_yi:.1f}亿 < {MIN_PROFIT_YI}亿"
    if m.positive_years < m.total_years:
        return False, 0.0, f"近{m.total_years}年有{m.total_years - m.positive_years}年亏损"
    if m.cfo_ratio is not None and m.cfo_ratio < MIN_CFO_RATIO:
        return False, 0.0, f"现金流/净利 {m.cfo_ratio:.2f} < {MIN_CFO_RATIO}"
    if m.profit_cagr <= 0:
        return False, 0.0, f"3年净利CAGR {m.profit_cagr:.1f}% <= 0"

    # 打分：ROE 占 40%、扣非 ROE 占 20%、净利润 CAGR 占 20%、现金流比 占 20%
    roe_score = min(m.roe_avg / 30.0 * 40, 40)
    excl_score = min(m.roe_excl_avg / 20.0 * 20, 20)
    cagr_score = min(max(m.profit_cagr, 0) / 20.0 * 20, 20)
    cfo_score = (
        min(max(m.cfo_ratio or 0, 0) / 1.0 * 20, 20) if m.cfo_ratio is not None else 10
    )
    score = roe_score + excl_score + cagr_score + cfo_score
    return True, round(score, 1), "逻辑成立"
