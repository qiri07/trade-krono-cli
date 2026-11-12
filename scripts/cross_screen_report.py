#!/usr/bin/env python3
"""流程交集 ∩ 六维选股 交叉验证报告（可复用）。

计算「当日多线共振交集（combined）」与「当日六维选股结果」的共有股票，
逐只输出流程命中线、六维评分、估值 / 位置 / 趋势 / 筹码 / 量价，并推送飞书：

  1. ``collect_source_sets`` + ``build_intersection`` → 流程交集池
  2. ``scripts/six_dim_screen.py`` 产出的 ``six_dim_screen_<YYYYMMDD>.json`` → 六维 Top N
  3. 两者取交集，卡片按六维**分档字典序**（逻辑 > 估值 > 位置 > 趋势 > 股东 > 量价）排序
  4. ``push_feishu`` 推送（``--no-push`` 可跳过）

用途：流程线偏「趋势/共振」，六维线偏「逻辑+估值+位置」，两者交集股票属于
低重合度交叉验证，通常只有个位数，适合人工重点复核。

用法：
  uv run python scripts/cross_screen_report.py                        # 最近交易日，推送
  uv run python scripts/cross_screen_report.py --date 2026-11-11      # 指定日期
  uv run python scripts/cross_screen_report.py --min-sources 2        # 降低流程交集门槛
  uv run python scripts/cross_screen_report.py --no-push              # 只生成卡片文件
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger

from scripts._utils import expected_trading_day
from scripts.combined_stock_collect import _SOURCE_LABELS, collect_source_sets
from scripts.combined_stock_intersect import build_intersection
from scripts.combined_stock_models import StockCandidate
from scripts.combined_stock_report import load_analyzed_details, normalize_source_sets
from scripts.feishu_utils import push_feishu
from scripts.six_dim_screen import DIM_PRIORITY, TIER_WIDTH

_SIX_DIM_DIMS: tuple[str, ...] = (
    "logic",
    "valuation",
    "position",
    "trend",
    "holder",
    "vol_price",
)
_DIM_LABELS: dict[str, str] = {
    "logic": "逻辑",
    "valuation": "估值",
    "position": "位置",
    "trend": "趋势",
    "holder": "股东",
    "vol_price": "量价",
}


@dataclass(frozen=True)
class SixDimRow:
    """六维选股单只结果（仅保留卡片需要的字段）。"""

    code: str
    name: str
    ticker: str
    composite: float
    dims: dict[str, float]
    metrics: dict[str, object]
    notes: dict[str, str]


def _as_float(payload: dict[str, object], key: str) -> float:
    """从 payload 取 float，非数字返回 0.0。"""
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    return float(value)


def _as_str(payload: dict[str, object], key: str) -> str:
    """从 payload 取字符串，缺失返回空串。"""
    value = payload.get(key)
    return value if isinstance(value, str) else ""


def load_six_dim(path: Path) -> list[SixDimRow]:
    """读取 six_dim_screen JSON 的 results 列表。

    Args:
        path: ``outputs/results/six_dim_screen_<YYYYMMDD>.json``。

    Returns:
        六维结果行（按文件顺序）；文件缺失或解析失败返回空列表。
    """
    if not path.exists():
        logger.error(f"❌ 六维选股结果文件缺失：{path}")
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.error(f"❌ 六维选股结果解析失败：{path}，{e}")
        return []
    rows: list[SixDimRow] = []
    results = data.get("results", []) if isinstance(data, dict) else []
    for item in results:
        if not isinstance(item, dict):
            continue
        raw_metrics = item.get("metrics")
        raw_notes = item.get("notes")
        metrics = raw_metrics if isinstance(raw_metrics, dict) else {}
        notes = raw_notes if isinstance(raw_notes, dict) else {}
        ticker = _as_str(item, "ticker")
        code = _as_str(item, "code") or (ticker.split(".")[-1] if "." in ticker else ticker)
        if not code:
            continue
        rows.append(
            SixDimRow(
                code=code,
                name=_as_str(item, "name") or code,
                ticker=ticker or code,
                composite=_as_float(item, "composite"),
                dims={d: _as_float(item, d) for d in _SIX_DIM_DIMS},
                metrics={k: v for k, v in metrics.items() if isinstance(k, str)},
                notes={k: v for k, v in notes.items() if isinstance(k, str) and isinstance(v, str)},
            )
        )
    logger.info(f"📄 六维选股结果：{len(rows)} 只（{path.name}）")
    return rows


def _fmt_float(metrics: dict[str, object], key: str) -> float | None:
    """取 metrics 中的数值字段（缺失返回 None）。"""
    value = metrics.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _candidate_for(pool: dict[str, StockCandidate], row: SixDimRow) -> StockCandidate | None:
    """在流程池中查找六维行对应候选（兼容带前缀 / 裸代码两种写法）。"""
    for key in (row.ticker, row.code, f"sh.{row.code}", f"sz.{row.code}"):
        cand = pool.get(key)
        if cand is not None:
            return cand
    return None


def tiered_key(row: SixDimRow) -> tuple[float, ...]:
    """六维分档字典序键，与 :mod:`scripts.six_dim_screen` 的排序口径保持一致。

    六维各按 ``TIER_WIDTH`` 分一档依次比较（逻辑 → 估值 → 位置 → 趋势 → 股东 → 量价），
    六维全同档时才用综合分兜底。
    """
    return (
        *(float(int(row.dims.get(dim, 0.0) // TIER_WIDTH)) for dim in DIM_PRIORITY),
        row.composite,
    )


def select_hits(pool: dict[str, StockCandidate], rows: list[SixDimRow]) -> list[SixDimRow]:
    """返回与流程池有交集的六维行，按六维分档字典序（逻辑优先）排序。"""
    hits = [r for r in rows if _candidate_for(pool, r) is not None]
    hits.sort(key=tiered_key, reverse=True)
    return hits


def build_cross_card(
    date_str: str,
    pool: dict[str, StockCandidate],
    rows: list[SixDimRow],
    min_sources: int,
    details: dict[str, StockCandidate] | None = None,
) -> str:
    """构建交集卡片正文（流程命中 + 六维评分 + 估值/位置/趋势/筹码/量价）。

    Args:
        date_str: 日期字符串（YYYY-MM-DD）。
        pool: 流程交集池 ``{带前缀 ticker: StockCandidate}``。
        rows: 六维选股结果行。
        min_sources: 流程交集门槛（用于口径说明）。
        details: combined 明细映射（含技术面 / 基本面），可为空。

    Returns:
        飞书卡片正文（纯文本）。
    """
    details = details or {}
    hits = select_hits(pool, rows)

    lines: list[str] = []
    lines.append(f"🎯 {date_str} 流程结果 ∩ 六维选股 交集")
    lines.append("")
    lines.append("口径：")
    lines.append(f"  · 流程结果：当日多线共振交集（命中 ≥{min_sources} 条选股线）{len(pool)} 只")
    lines.append(
        f"  · 六维选股：分档字典序 Top {len(rows)}（每维 {TIER_WIDTH:.0f} 分一档，"
        "逻辑 > 估值 > 位置 > 趋势 > 股东 > 量价）"
    )
    lines.append(f"  · 交集：{len(hits)} 只（按六维分档字典序排序）")
    lines.append("")

    if not hits:
        lines.append("⚠️ 两套口径今日无交集股票。")
        return "\n".join(lines)

    for i, row in enumerate(hits, 1):
        cand = _candidate_for(pool, row)
        hit_count = cand.hit_count if cand is not None else 0
        sources = cand.sources if cand is not None else []
        labels = "+".join(_SOURCE_LABELS.get(s, s) for s in sources)
        lines.append(f"{i}. {row.name}（{row.ticker}）- 流程内 {hit_count} 条线")
        if labels:
            lines.append(f"   命中：{labels}")
        dim_text = " ".join(
            f"{_DIM_LABELS[d]}{row.dims[d]:.1f}" for d in _SIX_DIM_DIMS if d in row.dims
        )
        lines.append(f"   六维：综合 {row.composite:.1f}（{dim_text}）")

        pe = _fmt_float(row.metrics, "pe")
        pb = _fmt_float(row.metrics, "pb")
        peg = _fmt_float(row.metrics, "peg")
        pe_pct = _fmt_float(row.metrics, "pe_pct")
        roe = _fmt_float(row.metrics, "roe_avg")
        cfo = _fmt_float(row.metrics, "cfo_ratio")
        parts: list[str] = []
        if pe is not None:
            seg = f"PE={pe:.1f}"
            if pe_pct is not None:
                seg += f"（五年分位{pe_pct:.0f}%）"
            parts.append(seg)
        if pb is not None:
            parts.append(f"PB={pb:.2f}")
        if peg is not None:
            parts.append(f"PEG={peg:.2f}")
        if roe is not None:
            parts.append(f"ROE均值={roe:.1f}%")
        if cfo is not None:
            parts.append(f"现金流/净利={cfo:.2f}")
        if parts:
            lines.append(f"   估值：{'  '.join(parts)}")

        for key, label in (
            ("position", "位置"),
            ("trend", "趋势"),
            ("holder", "筹码"),
            ("vol_price", "量价"),
        ):
            note = row.notes.get(key)
            if note:
                lines.append(f"   {label}：{note}")

        detail = details.get(row.ticker) or details.get(row.code)
        if detail is not None and detail.analysis is not None:
            tv = detail.tech
            lines.append(
                f"   技术面：{tv.score:.0f}分 趋势={tv.trend} 均线={tv.ma_signal}"
                f" 量能={tv.vol_signal}"
            )
            fv = detail.fund
            if fv.valuation_conclusion:
                lines.append(f"   估值结论：{fv.valuation_conclusion}")
        lines.append("")

    lines.append("---")
    lines.append(
        f"说明：流程结果取当日 combined 交集（≥{min_sources} 条线）；"
        f"六维取当日综合评分 Top {len(rows)}。"
    )
    lines.append(
        "交集通常只有个位数，属低重合度交叉验证：流程线看趋势共振，六维线看逻辑+估值+位置，"
        "两套口径同时命中的股票值得人工重点复核。"
    )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    """构造 CLI 参数解析器。"""
    parser = argparse.ArgumentParser(description="流程交集 ∩ 六维选股 交叉验证（卡片 + 飞书推送）")
    parser.add_argument(
        "--date",
        default=expected_trading_day(),
        help="目标日期 YYYY-MM-DD，默认最近交易日",
    )
    parser.add_argument(
        "--min-sources",
        type=int,
        default=3,
        help="流程交集最少命中选股线条数（默认 3）",
    )
    parser.add_argument(
        "--no-push",
        action="store_true",
        help="只生成卡片文件，不推送飞书",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="卡片文本输出路径（默认 outputs/results/cross_flow_sixdim_<YYYYMMDD>.txt）",
    )
    return parser


def main() -> None:
    """计算交集 + 生成卡片 + 可选飞书推送。"""
    args = build_parser().parse_args()
    date_str = str(args.date)
    dc = date_str.replace("-", "")
    logger.info("=" * 60)
    logger.info(f"🎯 流程交集 ∩ 六维选股  日期={date_str}  min_sources={args.min_sources}")
    logger.info("=" * 60)

    source_sets = normalize_source_sets(collect_source_sets(date_str))
    cands = build_intersection(source_sets, args.min_sources)
    pool = {c.code: c for c in cands}
    logger.info(f"流程交集池（≥{args.min_sources} 条线）：{len(pool)} 只")

    rows = load_six_dim(Path(f"outputs/results/six_dim_screen_{dc}.json"))
    if not rows:
        logger.error(f"❌ 无六维选股结果，无法计算交集（日期 {date_str}）")
        sys.exit(1)

    details = load_analyzed_details(
        Path(f"outputs/results/combined_intersection_{dc}.json"), date_str
    )
    card = build_cross_card(date_str, pool, rows, args.min_sources, details)
    hits = select_hits(pool, rows)
    logger.info(f"交集命中：{len(hits)} 只")

    out_path = Path(args.out or f"outputs/results/cross_flow_sixdim_{dc}.txt")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(card, encoding="utf-8")
    logger.info(f"💾 卡片已保存：{out_path}")

    if args.no_push:
        logger.info("已跳过飞书推送（--no-push）")
        return

    ok = push_feishu("text", content=card, title=f"🎯 {date_str} 流程交集 ∩ 六维选股")
    logger.info(f"{'✅' if ok else '❌'} 飞书推送{'成功' if ok else '失败'}")


if __name__ == "__main__":
    main()
