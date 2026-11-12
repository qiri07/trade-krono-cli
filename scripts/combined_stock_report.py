#!/usr/bin/env python3
"""综合选股交集重放脚本（可复用）。

复用 combined_stock_analysis 的既有模块，仅做「交集计算 + 汇总卡片 + 飞书推送」，
不重跑技术分析 / AI 核实 / Kronos 预测，适合按日期重放或补推：

  1. ``collect_source_sets(date)``  读取各选股线当日结果文件 → 代码集合
  2. ``build_intersection``         统计命中 ≥ min_sources 条线的股票
  3. 若存在 ``combined_intersection_<date>.json``，读取其中 analyzed 明细作为
     前 10 名的技术面 / 基本面 / Kronos / 回测数据源
  4. ``_build_combined_card``       生成卡片（前 10 名详细，其余仅名称 + 交集次数）
  5. ``push_feishu``                推送（``--no-push`` 可跳过）

代码空间统一为带前缀 ticker（``sh.`` / ``sz.``），北交所已剔除，避免同一只股票
因裸代码 / 带前缀两种写法被重复计数。

用法：
  uv run python scripts/combined_stock_report.py                       # 最近交易日，推送
  uv run python scripts/combined_stock_report.py --date 2026-11-11     # 指定日期重放
  uv run python scripts/combined_stock_report.py --min-sources 2       # 降低交集门槛
  uv run python scripts/combined_stock_report.py --no-push             # 只生成卡片文件
  uv run python scripts/combined_stock_report.py --detail-json path.json  # 指定明细来源
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger

from scripts._utils import expected_trading_day, is_bse_code, normalize_ticker_key
from scripts.combined_stock_cards import _build_combined_card
from scripts.combined_stock_collect import _SOURCE_LABELS, collect_source_sets
from scripts.combined_stock_intersect import build_intersection
from scripts.combined_stock_models import BacktestView, StockCandidate
from scripts.feishu_utils import push_feishu
from trade_krono_cli.domain.kronos_result import KronosForecastResult


def _normalize_code(raw: str) -> str | None:
    """把 6 位代码或带前缀 ticker 归一化为带前缀 ticker（北交所返回 None）。

    Args:
        raw: ``600519`` / ``sh.600519`` / ``bj.920001`` 等任一写法。

    Returns:
        归一化后的 ticker（如 ``sh.600519``）；空值或北交所返回 ``None``。
    """
    text = raw.strip()
    if not text:
        return None
    if "." in text:
        prefix, _, code = text.partition(".")
        prefix = prefix.lower()
    else:
        prefix, code = "", text
    code = code.strip()
    if len(code) != 6 or not code.isdigit():
        return None
    if prefix == "bj" or is_bse_code(code):
        return None
    if prefix in {"sh", "sz", "hk"}:
        return f"{prefix}.{code}"
    return normalize_ticker_key(code)


def normalize_source_sets(
    source_sets: dict[str, set[str]],
) -> dict[str, set[str]]:
    """把各选股线代码集合统一为带前缀 ticker，并剔除北交所 / 非法代码。

    Args:
        source_sets: 原始 ``{来源键: 代码集合}``（可能混用裸代码与带前缀写法）。

    Returns:
        归一化后的来源集合，同一来源内已去重。
    """
    normalized: dict[str, set[str]] = {}
    for source, codes in source_sets.items():
        bucket: set[str] = set()
        for raw in codes:
            ticker = _normalize_code(raw)
            if ticker is not None:
                bucket.add(ticker)
        normalized[source] = bucket
    return normalized


def _as_float(payload: dict[str, object], key: str) -> float | None:
    """从 payload 取 float 字段（非数字视为 None）。"""
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _as_int(payload: dict[str, object], key: str, default: int = 0) -> int:
    """从 payload 取 int 字段（非数字返回默认值）。"""
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return int(value)


def _kronos_from_payload(
    payload: object, ticker: str, date_str: str
) -> KronosForecastResult | None:
    """从落盘 JSON 的 kronos 段还原 KronosForecastResult（无数据返回 None）。"""
    if not isinstance(payload, dict):
        return None
    direction = payload.get("direction")
    expected = _as_float(payload, "expected_change_pct")
    if not direction and expected is None:
        return None
    band = {
        "low": _as_float(payload, "confidence_low"),
        "high": _as_float(payload, "confidence_high"),
    }
    return KronosForecastResult(
        ticker=ticker,
        eval_date=date_str,
        horizon=_as_int(payload, "horizon"),
        last_close=_as_float(payload, "last_close"),
        predicted_close_final=_as_float(payload, "predicted_close"),
        expected_change_pct=expected,
        direction=str(direction) if direction else None,
        volatility_proxy=_as_float(payload, "volatility_proxy"),
        confidence_band=band,
    )


def _backtest_from_payload(payload: object) -> BacktestView | None:
    """从落盘 JSON 的 backtest 段还原 BacktestView（无数据返回 None）。"""
    if not isinstance(payload, dict) or not payload.get("available"):
        return None
    return BacktestView(
        total_return_pct=_as_float(payload, "total_return_pct"),
        sharpe_ratio=_as_float(payload, "sharpe_ratio"),
        max_drawdown_pct=_as_float(payload, "max_drawdown_pct"),
        win_rate_pct=_as_float(payload, "win_rate_pct"),
        n_trades=_as_int(payload, "n_trades"),
        n_signals=_as_int(payload, "n_signals"),
        horizon_days=_as_int(payload, "horizon_days"),
        window_days=_as_int(payload, "window_days"),
        available=True,
    )


def load_analyzed_details(
    path: Path, date_str: str
) -> dict[str, StockCandidate]:
    """读取 combined_intersection JSON 中的 analyzed 明细，供卡片前 10 名展示。

    Args:
        path: ``outputs/results/combined_intersection_<YYYYMMDD>.json``。
        date_str: 日期字符串（YYYY-MM-DD），用于还原 Kronos 结果。

    Returns:
        ``{裸代码 与 带前缀 ticker: StockCandidate}`` 双键索引；文件缺失返回空字典。
    """
    if not path.exists():
        logger.warning(f"⚠️ 明细文件缺失：{path}，前 10 名仅显示名称与交集次数")
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"⚠️ 明细文件解析失败：{path}，{e}")
        return {}

    analyzed = data.get("analyzed", []) if isinstance(data, dict) else []
    details: dict[str, StockCandidate] = {}
    for item in analyzed:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or "").strip()
        if not code:
            continue
        sources = [str(s) for s in item.get("sources") or []]
        cand = StockCandidate(
            code=code,
            hit_count=_as_int(item, "hit_count"),
            sources=sources,
            analysis={
                "name": str(item.get("name") or ""),
                "trend": str(item.get("trend") or ""),
                "ma_signal": str(item.get("ma_signal") or ""),
                "vol_signal": str(item.get("vol_signal") or ""),
                "score": item.get("score", 0),
                "pe_ttm": item.get("pe_ttm"),
                "pb": item.get("pb"),
                "valuation_score": item.get("valuation_score"),
                "valuation_conclusion": str(item.get("valuation_conclusion") or ""),
            },
        )
        cand.kronos = _kronos_from_payload(item.get("kronos"), code, date_str)
        cand.backtest = _backtest_from_payload(item.get("backtest"))
        details[code] = cand
        prefixed = _normalize_code(code)
        if prefixed is not None:
            details.setdefault(prefixed, cand)
    logger.info(f"📄 已加载明细：{len(analyzed)} 只（{path.name}）")
    return details


def build_parser() -> argparse.ArgumentParser:
    """构造 CLI 参数解析器。"""
    parser = argparse.ArgumentParser(description="综合选股交集重放（卡片 + 飞书推送）")
    parser.add_argument(
        "--date",
        default=expected_trading_day(),
        help="目标日期 YYYY-MM-DD，默认最近交易日",
    )
    parser.add_argument(
        "--min-sources",
        type=int,
        default=3,
        help="进入交集池的最少命中选股线条数（默认 3）",
    )
    parser.add_argument(
        "--detail-json",
        default=None,
        help="明细来源 JSON（默认 outputs/results/combined_intersection_<YYYYMMDD>.json）",
    )
    parser.add_argument(
        "--no-push",
        action="store_true",
        help="只生成卡片文件，不推送飞书",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="卡片文本输出路径（默认 outputs/results/combined_report_<YYYYMMDD>.txt）",
    )
    return parser


def main() -> None:
    """交集计算 + 卡片生成 + 可选飞书推送。"""
    args = build_parser().parse_args()
    date_str = str(args.date)
    logger.info("=" * 60)
    logger.info(
        f"🔗 综合选股交集重放  日期={date_str}  min_sources={args.min_sources}"
    )
    logger.info("=" * 60)

    source_sets = normalize_source_sets(collect_source_sets(date_str))
    logger.info(
        "各选股线命中数量："
        + " ".join(
            f"{_SOURCE_LABELS.get(k, k)}={len(v)}" for k, v in source_sets.items()
        )
    )

    cands = build_intersection(source_sets, args.min_sources)
    logger.info(f"命中 ≥{args.min_sources} 条选股线的股票：{len(cands)} 只")
    for cand in cands[:20]:
        logger.info(
            f"  🔗 {cand.code}  命中 {cand.hit_count} 条: {'+'.join(cand.sources)}"
        )
    if len(cands) > 20:
        logger.info(f"  ...（其余 {len(cands) - 20} 只略）")

    dc = date_str.replace("-", "")
    detail_path = (
        Path(args.detail_json)
        if args.detail_json
        else Path(f"outputs/results/combined_intersection_{dc}.json")
    )
    details = load_analyzed_details(detail_path, date_str)

    card = _build_combined_card(
        cands,
        date_str,
        source_sets,
        total=len(cands),
        min_sources=args.min_sources,
        analyzed_map=details,
    )

    out_path = Path(args.out or f"outputs/results/combined_report_{dc}.txt")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(card, encoding="utf-8")
    logger.info(f"💾 卡片已保存：{out_path}")

    if args.no_push:
        logger.info("已跳过飞书推送（--no-push）")
        return

    ok = push_feishu("text", content=card, title=f"🔗 {date_str} 多线共振综合选股")
    logger.info(f"{'✅' if ok else '❌'} 飞书汇总推送{'成功' if ok else '失败'}")


if __name__ == "__main__":
    main()
