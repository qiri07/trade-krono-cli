#!/usr/bin/env python3
"""combined_stock_analysis 卡片构建模块。

包含：
- _build_stock_card: 单只股票飞书卡片文本（技术面+基本面+Kronos+回测+AI 核实）
- _push_per_stock: 逐只飞书推送（间隔 1s 防 webhook 限流）
- _build_combined_card: 飞书汇总卡片文本
- _llm_view: LLM 核实输入视图
"""

from __future__ import annotations

import json
import time
from typing import cast

from loguru import logger

from scripts._utils import get_cache_db_path, get_sqlite_conn, normalize_ticker_key
from scripts.combined_stock_collect import _SOURCE_LABELS
from scripts.combined_stock_models import StockCandidate
from scripts.feishu_utils import push_feishu
from scripts.stock_name_cache import (
    ensure_stock_name_table,
    get_cached_stock_name,
)

_META_CACHE: dict[str, dict[str, object | None]] | None = None


def _meta_keys(ticker: str) -> list[str]:
    """构造在 metadata.json 中尝试的 key 列表：原样 + 各交易所前缀。"""
    keys: list[str] = [ticker]
    if not ticker.startswith(("sh.", "sz.", "bj.", "hk.")):
        keys.extend([f"sh.{ticker}", f"sz.{ticker}", f"bj.{ticker}"])
    return keys


def _load_meta() -> dict[str, dict[str, object | None]]:
    """一次性加载 metadata.json 中所有需要的字段（name, dividend_yield）。

    返回 {key: {field: value}}，同时索引裸代码，避免每次查找都重复读磁盘。
    """
    global _META_CACHE
    if _META_CACHE is not None:
        return _META_CACHE

    cache: dict[str, dict[str, object | None]] = {}
    meta_path = get_cache_db_path().parent / "metadata.json"
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            for k, v in meta.items():
                if not isinstance(v, dict):
                    continue
                raw_name: object | None = v.get("name")
                raw_dy: object | None = v.get("dividend_yield")
                entry = cast(
                    "dict[str, object | None]",
                    {
                        "name": raw_name if isinstance(raw_name, str) else None,
                        "dividend_yield": (
                            float(raw_dy)
                            if isinstance(raw_dy, (int, float)) and not isinstance(raw_dy, bool)
                            else None
                        ),
                    },
                )
                cache[k] = entry
                bare = k.split(".", 1)[-1] if "." in k else k
                cache.setdefault(bare, entry)
        except (json.JSONDecodeError, OSError, ValueError) as e:
            logger.debug(f"读取 metadata.json 失败: {e}")

    _META_CACHE = cache
    return cache


def _get_meta_field(ticker: str, field: str) -> object | None:
    """从 metadata.json 获取指定字段，缺失返回 None。

    同时尝试带前缀（sh./sz.）、裸代码两种 key 格式，确保能命中缓存。
    """
    meta = _load_meta()
    for key in _meta_keys(ticker):
        entry = meta.get(key)
        if isinstance(entry, dict):
            val = entry.get(field)
            if val is not None:
                return val
    return None


def _get_stock_name_from_metadata(ticker: str) -> str:
    """获取股票名称，优先本地 SQLite 缓存，缺失再从 metadata.json 兜底。

    同时尝试带前缀（sh./sz.）和裸代码两种 key 格式，确保能命中缓存：
    SQLite 缓存 key 为带前缀 ticker（如 sh.600519），而调用方可能传入裸代码（如 600519）。

    作为 analyze_stock 返回 name 为空时的兜底。
    """
    # 构造要尝试的 key：原样、加 sh. 前缀、加 sz. 前缀、加 bj. 前缀
    keys: list[str] = _meta_keys(ticker)

    # 1. 优先本地 SQLite 名称缓存（命中不联网，实现「每日仅补漏」）
    try:
        _conn = get_sqlite_conn(get_cache_db_path())
        try:
            ensure_stock_name_table(_conn)
            for key in keys:
                name = get_cached_stock_name(_conn, key)
                if isinstance(name, str) and name:
                    return name
        finally:
            _conn.close()
    except Exception as e:
        logger.debug(f"{ticker} 从本地名称缓存读取失败: {e}")

    # 2. 从 metadata.json 兜底（已同时索引带前缀和裸代码）
    meta_name: object | None = _get_meta_field(ticker, "name")
    if isinstance(meta_name, str):
        return meta_name
    return ""


def _get_dividend_yield_from_metadata(ticker: str) -> float | None:
    """从 metadata.json 获取股票股息率（%），缺失返回 None。

    同时尝试带前缀（sh./sz.）、裸代码两种 key 格式，确保能命中缓存。
    """
    val = _get_meta_field(ticker, "dividend_yield")
    return float(val) if isinstance(val, (int, float)) and not isinstance(val, bool) else None


def _dividend_yield_line(code: str) -> str:
    """返回股息率卡片行文本：有数据则显示百分比，否则显示无股息数据。

    调用方按自身缩进拼接到卡片行中。
    """
    dy = _get_dividend_yield_from_metadata(code)
    if dy is not None:
        return f"股息率: {dy:.2f}%"
    return "股息率: 无股息数据"


def _build_stock_card(cand: StockCandidate, date_str: str, ai_note: str = "") -> str:
    """构建单只股票的飞书卡片文本（技术面 + 基本面 + Kronos 预测 + 历史回测 + AI 核实）。"""
    tv = cand.tech
    fv = cand.fund
    kv = cand.kronos_view
    bv = cand.backtest
    code = cand.code
    name = tv.name or _get_stock_name_from_metadata(code) or code
    label = "+".join(_SOURCE_LABELS.get(s, s) for s in cand.sources)

    lines: list[str] = []
    lines.append(f"🔗 **{date_str} 综合选股 · {code} {name}**")
    lines.append("")
    lines.append(f"命中来源：{label}（{cand.hit_count} 条线共振）")
    lines.append("")
    lines.append("── 技术面 ──")
    lines.append(f"  技术分: {tv.score}  趋势: {tv.trend}")
    lines.append(f"  均线信号: {tv.ma_signal}  量能: {tv.vol_signal}")
    lines.append("")
    lines.append("── 基本面 ──")
    lines.append(f"  {fv.summary_line()}")
    lines.append(f"  {_dividend_yield_line(code)}")
    if kv.available:
        lines.append("")
        lines.append("── Kronos 预测 ──")
        lines.append(f"  {kv.summary_line()}")
    if bv is not None:
        lines.append("")
        lines.append("── 历史回测 ──")
        lines.append(f"  {bv.summary_line()}")
    if ai_note:
        lines.append("")
        lines.append("── AI 核实 ──")
        lines.append(f"  {ai_note}")
    return "\n".join(lines)


def _push_per_stock(cands: list[StockCandidate], date_str: str, limit: int = 50) -> None:
    """逐只推送飞书卡片（每只含技术面 + 基本面），间隔 1s 防 webhook 限流。

    Args:
        cands: 候选列表（已按交集次数降序）。
        date_str: 日期字符串（YYYY-MM-DD）。
        limit: 最多推送前 N 只；<=0 表示不限。
    """
    targets = cands if limit <= 0 else cands[:limit]
    logger.info(
        f"📤 逐只推送：交集候选 {len(cands)} 只，"
        f"按交集次数只推前 {len(targets)} 只..."
    )
    for i, cand in enumerate(targets, 1):
        name = cand.tech.name or _get_stock_name_from_metadata(cand.code) or cand.code
        stock_card = _build_stock_card(cand, date_str)
        title = f"🔗 {date_str} 综合选股 {i}/{len(targets)} {cand.code} {name}"
        ok = push_feishu("text", content=stock_card, title=title)
        logger.info(f"  {'✅' if ok else '❌'} [{i}/{len(targets)}] {cand.code} {name}")
        if i < len(cands):
            time.sleep(1)


def _build_combined_card(
    cands: list[StockCandidate],
    date_str: str,
    source_sets: dict[str, set[str]],
    total: int | None = None,
    min_sources: int = 3,
    analyzed_map: dict[str, StockCandidate] | None = None,
) -> str:
    """构建飞书汇总卡片文本（含技术面 + 基本面 + AI 核实）。

    前 10 名保留完整分析（技术面/基本面/Kronos 预测/历史回测），其余候选
    仅列出名称与交集次数，不展开明细。

    Args:
        cands: 交集池全部候选列表（命中 ≥min_sources 条选股线）。
        date_str: 日期字符串（YYYY-MM-DD）。
        source_sets: 各选股线命中的股票集合，用于统计每条线命中数。
        total: 交集池股票总数（≥min_sources 条线）；缺省时取 ``len(cands)``。
        min_sources: 进入交集池的最少命中条数，用于标题表述。
        analyzed_map: 已做技术分析的候选映射（code -> StockCandidate），用于
            前 10 名的详细数据；未在此映射中的只显示基础信息。

    Returns:
        飞书卡片正文（纯文本）。
    """
    analyzed_map = analyzed_map or {}
    lines: list[str] = []
    lines.append(f"🔗 **{date_str} 综合选股 · 多线共振汇总**")
    lines.append("")
    lines.append("各选股线命中数量：")
    for key, label in _SOURCE_LABELS.items():
        n = len(source_sets.get(key, set()))
        lines.append(f"  · {label}：{n} 只")
    lines.append("")

    if not cands:
        lines.append(f"⚠️ 无命中 ≥{min_sources} 条选股线的股票。")
        return "\n".join(lines)

    total_n = total if total is not None else len(cands)
    header = (
        f"📊 命中 ≥{min_sources} 条线共 {total_n} 只，"
        f"按交集次数降序（前 10 名详细分析，其余仅列出名称与交集次数）"
    )
    lines.append(header)
    lines.append("")
    # 按交集次数降序排列
    sorted_cands = sorted(cands, key=lambda c: c.hit_count, reverse=True)
    # 前 10 名保留完整分析，其余仅列出名称与交集次数
    detailed = sorted_cands[:10]
    rest = sorted_cands[10:]
    for i, cand in enumerate(detailed, 1):
        code = cand.code
        # 优先从 analyzed_map 查找已做技术分析的数据
        # 尝试多种代码格式：带前缀（sh.002911）、不带前缀（002911）、另一交易所前缀（sz.002911->sh.002911）
        detail_cand = analyzed_map.get(code)
        if detail_cand is None:
            # 尝试去掉前缀
            if code.startswith("sh.") or code.startswith("sz."):
                detail_cand = analyzed_map.get(code[3:])
            # 尝试添加前缀
            else:
                detail_cand = analyzed_map.get(f"sh.{code}") or analyzed_map.get(f"sz.{code}")
        # 交集池里的候选本身可能已带分析结果（未经 analyzed_map 中转）
        if detail_cand is None and cand.analysis is not None:
            detail_cand = cand
        if detail_cand is not None and detail_cand.analysis is not None:
            tv = detail_cand.tech
            fv = detail_cand.fund
            name = tv.name or code
            label = "+".join(_SOURCE_LABELS.get(s, s) for s in cand.sources)
            lines.append(f"  {i:3d}. {name}（{code}）-{cand.hit_count}次交集")
            lines.append(
                f"     技术面: {tv.score}分 趋势={tv.trend} 均线={tv.ma_signal} 量能={tv.vol_signal}"
            )
            # 站稳5日线提示：收盘价 > MA5 视为站稳
            ma5_txt = "-"
            if tv.close is not None and tv.ma5 is not None:
                ma5_txt = "✅ 站稳5日线" if tv.close > tv.ma5 else "❌ 未站稳5日线"
            lines.append(f"     站稳5日线: {ma5_txt} (close={tv.close if tv.close is not None else '-'}, MA5={tv.ma5 if tv.ma5 is not None else '-'})")
            lines.append(f"     基本面: {fv.summary_line()}")
            lines.append(f"     {_dividend_yield_line(code)}")
            kv = detail_cand.kronos_view
            if kv.available:
                # 部分预测结果只有方向、缺少幅度/置信区间，直接格式化 None 会抛 TypeError，
                # 进而整张汇总卡片推送失败。
                if kv.expected_change_pct is not None and kv.confidence_low is not None and kv.confidence_high is not None:
                    lines.append(
                        f"     预测: {kv.direction} {kv.expected_change_pct:+.1f}% "
                        f"({kv.confidence_low:+.1f}%~{kv.confidence_high:+.1f}%)"
                    )
                else:
                    lines.append(f"     预测: {kv.summary_line()}")
            bt = detail_cand.backtest
            if bt and bt.available:
                lines.append(
                    f"     回测: 收益={bt.total_return_pct:+.1f}% 夏普={bt.sharpe_ratio:.2f} "
                    f"最大回撤={bt.max_drawdown_pct:+.1f}% 胜率={bt.win_rate_pct:.1f}%"
                    if all(
                        v is not None
                        for v in (
                            bt.total_return_pct,
                            bt.sharpe_ratio,
                            bt.max_drawdown_pct,
                            bt.win_rate_pct,
                        )
                    )
                    else "     回测: 数据不完整"
                )
        else:
            # 没有详细分析数据，尝试从 metadata 获取名称，否则只显示代码
            name = _get_stock_name_from_metadata(code) or code
            lines.append(f"  {i:3d}. {name}（{code}）-{cand.hit_count}次交集")
    if rest:
        lines.append("")
        lines.append("---")
        lines.append("其余候选（仅列出名称与交集次数）：")
        for i, cand in enumerate(rest, len(detailed) + 1):
            code = cand.code
            # 优先从 analyzed_map 查找名称；找不到则从 metadata.json 查找；都没有则用代码
            detail_cand = analyzed_map.get(code)
            if detail_cand is None:
                if code.startswith("sh.") or code.startswith("sz."):
                    detail_cand = analyzed_map.get(code[3:])
                else:
                    detail_cand = analyzed_map.get(f"sh.{code}") or analyzed_map.get(f"sz.{code}")
            if detail_cand and detail_cand.tech.name:
                name = detail_cand.tech.name
            else:
                name = _get_stock_name_from_metadata(code) or code
            lines.append(f"  {i:3d}. {name}（{code}）-{cand.hit_count}次交集")
    return "\n".join(lines)


def _llm_view(c: StockCandidate) -> dict[str, object]:
    """将候选转为 LLM 核实输入（ticker + 技术 + 基本面 + Kronos 预测指标）。

    Kronos 不可用时不包含 kronos 键，供 LLM 端区分「无数据」与「有数据」。
    """
    view = c.tech.to_payload()
    fund = c.fund.to_payload()
    payload: dict[str, object] = {"ticker": normalize_ticker_key(c.code), **view, **fund}
    kv = c.kronos_view
    if kv.available:
        payload["kronos"] = kv.to_payload()
    return payload
