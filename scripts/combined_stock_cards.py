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
from pathlib import Path

from loguru import logger

from scripts._utils import normalize_ticker_key
from scripts.combined_stock_collect import _SOURCE_LABELS
from scripts.combined_stock_models import StockCandidate
from scripts.feishu_utils import push_feishu

_META_NAMES: dict[str, str] | None = None


def _load_meta_names() -> dict[str, str]:
    """一次性加载 metadata.json 中所有股票名称，作为 analyze_stock 返回 name 为空时的兜底。

    metadata.json 的 key 为带前缀 ticker（如 sh.600519 / sz.002832 / hk.00700），
    value 包含 name 字段。同时也索引不带前缀的裸代码（如 002832），
    避免每次查找都重复读取磁盘文件。
    """
    global _META_NAMES
    if _META_NAMES is not None:
        return _META_NAMES

    names: dict[str, str] = {}
    meta_path = Path("outputs/cache/metadata.json")
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            for k, v in meta.items():
                if isinstance(v, dict) and v.get("name"):
                    names[k] = v["name"]
                    # 同时索引裸代码（如 002832），便于 analyze_stock 返回的无前缀代码查找
                    bare = k.split(".", 1)[-1] if "." in k else k
                    names.setdefault(bare, v["name"])
        except (json.JSONDecodeError, OSError, ValueError) as e:
            logger.debug(f"读取 metadata.json 失败: {e}")

    _META_NAMES = names
    return names


def _get_stock_name_from_metadata(ticker: str) -> str:
    """从 metadata.json 中查找股票名称，作为 analyze_stock 返回 name 为空时的兜底。

    metadata.json 的 key 为带前缀 ticker（如 sh.600519 / sz.002832 / hk.00700），
    value 包含 name 字段。
    """
    return _load_meta_names().get(ticker, "")


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


def _push_per_stock(cands: list[StockCandidate], date_str: str) -> None:
    """逐只推送飞书卡片（每只含技术面 + 基本面），间隔 1s 防 webhook 限流。"""
    logger.info(f"📤 开始逐只推送 {len(cands)} 只股票...")
    for i, cand in enumerate(cands, 1):
        name = cand.tech.name or _get_stock_name_from_metadata(cand.code) or cand.code
        stock_card = _build_stock_card(cand, date_str)
        title = f"🔗 {date_str} 综合选股 {i}/{len(cands)} {cand.code} {name}"
        ok = push_feishu("text", content=stock_card, title=title)
        logger.info(f"  {'✅' if ok else '❌'} [{i}/{len(cands)}] {cand.code} {name}")
        if i < len(cands):
            time.sleep(1)


def _build_combined_card(
    cands: list[StockCandidate], date_str: str, source_sets: dict[str, set[str]]
) -> str:
    """构建飞书汇总卡片文本（含技术面 + 基本面 + AI 核实）。"""
    lines: list[str] = []
    lines.append(f"🔗 **{date_str} 综合选股 · 多线共振汇总**")
    lines.append("")
    lines.append("各选股线命中数量：")
    for key, label in _SOURCE_LABELS.items():
        n = len(source_sets.get(key, set()))
        lines.append(f"  · {label}：{n} 只")
    lines.append("")

    if not cands:
        lines.append("⚠️ 无命中 ≥3 条选股线的股票。")
        return "\n".join(lines)

    lines.append(f"📊 命中 ≥3 条选股线的股票（{len(cands)} 只，按共振度排序）：")
    lines.append("")
    for cand in cands:
        code = cand.code
        tv = cand.tech
        fv = cand.fund
        name = tv.name or _get_stock_name_from_metadata(code) or code
        label = "+".join(_SOURCE_LABELS.get(s, s) for s in cand.sources)
        if cand.analysis:
            lines.append(f"  🔗 **{code} {name}**（{label}）")
            lines.append(
                f"     技术分={tv.score}  趋势={tv.trend}  "
                f"均线={tv.ma_signal}  量能={tv.vol_signal}"
            )
            lines.append(f"     基本面: {fv.summary_line()}")
            kv = cand.kronos_view
            if kv.available:
                lines.append(f"     预测: {kv.summary_line()}")
            bv = cand.backtest
            if bv is not None:
                lines.append(f"     回测: {bv.summary_line()}")
        else:
            lines.append(f"  🔗 **{code} {name}**（{label}，无技术数据）")
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
