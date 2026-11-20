"""CLI 命令 — a_stock_screen：A股流动性/市值/停牌/ST/北交所/ETF 硬过滤。

参考 scripts/hk_screen/cli.py，为 A股 提供独立的流动性筛选命令，
可在今日流程之外直接通过 `uv run trade-krono-cli a-stock-screen` 调用。

过滤标准（参考港股筛选逻辑，改写为 A股 市场）：
  - 近 20 个交易日日均成交额 >= 5000 万元
  - 近 12 个月日均换手率 >= 0.1%
  - 总市值 >= 50 亿元
  - 剔除 ST / 北交所 / ETF / 过度停牌 / 低价股
"""

from __future__ import annotations

import sys
from pathlib import Path

import typer
from loguru import logger
from rich.console import Console

# 确保可 import scripts 包（与 scripts/a_stock_screen/cli.py 一致的 sys.path 做法）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from scripts._utils import expected_trading_day, get_all_tickers, load_metadata_index  # noqa: E402
from scripts.a_stock_screen import AStockScreenResult, screen_a_stock_candidates  # noqa: E402

console: Console = Console()


def _fmt(val, pct: bool = False) -> str:
    """安全格式化单元格值。"""
    if val is None:
        return "N/A"
    try:
        if pct:
            return f"{float(val):.1%}"
        if isinstance(val, float):
            return f"{val:,.2f}"
        return str(val)
    except (TypeError, ValueError):
        return "N/A"


def _build_row(r: AStockScreenResult) -> dict[str, str]:
    """把筛选结果转为展示用的字符串行。"""
    return {
        "code": r.code,
        "name": r.name,
        "close": _fmt(r.close),
        "market_cap": _fmt(r.market_cap / 1e8 if r.market_cap is not None else None) + "亿"
        if r.market_cap is not None
        else "N/A",
        "pe_ttm": _fmt(r.pe_ttm),
        "pb": _fmt(r.pb),
        "turnover_20d_avg": _fmt(r.turnover_20d_avg / 1e4 if r.turnover_20d_avg is not None else None) + "万"
        if r.turnover_20d_avg is not None
        else "N/A",
        "turnover_rate_12m_avg": _fmt(r.turnover_rate_12m_avg, pct=True),
        "listing_days": str(r.listing_days) if r.listing_days is not None else "N/A",
        "suspension_days_30d": str(r.suspension_days_30d),
        "is_st": "是" if r.is_st else "否",
        "is_etf": "是" if r.is_etf else "否",
        "is_bse": "是" if r.is_bse else "否",
        "pass_count": str(r.pass_count),
        "total_rules": str(r.total_rules),
        "fail_reasons": r.fail_reasons[0] if r.fail_reasons else "",
        "passed": "是" if r.passed else "否",
    }


def a_stock_screen(
    codes: str | None = typer.Option(
        None,
        "--codes",
        help="6 位裸代码，逗号分隔（如 600519,000858）",
    ),
    all_stocks: bool = typer.Option(
        False,
        "--all",
        help="对缓存中的全部股票进行筛选",
    ),
    date: str = typer.Option(
        None,
        "--date",
        help="分析日期 YYYY-MM-DD（默认最近交易日）",
    ),
    top: int = typer.Option(
        0,
        "--top",
        help="只输出前 N 名（0=全部）",
    ),
    output: str | None = typer.Option(
        None,
        "--output",
        help="输出 CSV 文件路径（不指定则不保存）",
    ),
    min_pass_rate: float = typer.Option(
        1.0,
        "--min-pass-rate",
        help="最小通过比例（1.0=必须通过所有规则，0.8=通过80%以上规则）",
    ),
    no_push: bool = typer.Option(
        False,
        "--no-push",
        help="不推送飞书",
    ),
) -> None:
    """A股流动性/市值/停牌/ST/北交所/ETF 硬过滤筛选。"""
    date_str = date or expected_trading_day()
    logger.info(f"🔍 A股筛选启动  日期={date_str}")

    # 确定待筛选代码
    if codes:
        cand_codes = [c.strip() for c in codes.split(",") if c.strip()]
    elif all_stocks:
        cand_codes = get_all_tickers()
    else:
        console.print("[red]❌ 必须指定 --codes 或 --all[/red]")
        raise typer.Exit(1)

    logger.info(f"📋 待筛选股票数量: {len(cand_codes)} 只")

    # 加载元数据并执行筛选
    meta = load_metadata_index()
    passed_codes, details = screen_a_stock_candidates(cand_codes, date_str, metadata=meta)

    # 统计并排序全部结果（按 pass_count 降序、fail_reasons 长度升序）
    results: list[AStockScreenResult] = list(details.values())
    results.sort(key=lambda r: (-r.pass_count, len(r.fail_reasons)))

    # 按最小通过比例过滤
    filtered = [
        r
        for r in results
        if r.total_rules == 0 or (r.pass_count / r.total_rules) >= min_pass_rate
    ]

    passed = [r for r in filtered if r.passed]
    top_results = filtered[:top] if top > 0 else filtered

    # 打印摘要
    console.print("=" * 60)
    console.print("A股筛选结果摘要")
    console.print("=" * 60)
    console.print(f"候选股票总数: {len(results)} 只")
    console.print(f"通过所有规则: {len(passed)} 只")
    console.print(f"满足最小通过比例({min_pass_rate:.0%}): {len(filtered)} 只")
    console.print("=" * 60)

    if top_results:
        console.print("\n📋 筛选通过股票明细:")
        for i, r in enumerate(top_results, 1):
            row = _build_row(r)
            passed_mark = "[green]是[/green]" if r.passed else "[red]否[/red]"
            console.print(
                f"  {i:3d}. {row['code']} {row['name']:<12} "
                f"市值={row['market_cap']} "
                f"PE={row['pe_ttm']} "
                f"成交额20d={row['turnover_20d_avg']} "
                f"换手率12m={row['turnover_rate_12m_avg']} "
                f"规则={row['pass_count']}/{row['total_rules']} "
                f"通过={passed_mark}"
            )
            if r.fail_reasons:
                console.print(f"         失败原因: {'; '.join(r.fail_reasons)}")

    # 保存到 CSV
    if output:
        import pandas as pd

        df = pd.DataFrame([r.to_dict() for r in filtered])
        _OUTPUT_FIELDS: list[str] = [
            "code",
            "name",
            "close",
            "market_cap",
            "pe_ttm",
            "pb",
            "turnover_20d_avg",
            "turnover_rate_12m_avg",
            "listing_days",
            "suspension_days_30d",
            "is_st",
            "is_etf",
            "is_bse",
            "pass_count",
            "total_rules",
            "fail_reasons",
            "passed",
        ]
        exist_cols = [c for c in _OUTPUT_FIELDS if c in df.columns]
        df = df[exist_cols]
        df.to_csv(output, index=False, encoding="utf-8-sig")
        console.print(f"💾 结果已保存到: {output}（{len(filtered)} 只）")

    # 飞书推送（如果启用）
    if not no_push and passed:
        try:
            from scripts.feishu_utils import push_feishu  # noqa: PLC0415

            lines = [f"📈 **A股筛选结果**（共 {len(passed)} 只通过所有规则）\n"]
            for r in top_results[:20]:
                cap = f"{r.market_cap / 1e8:.0f}亿" if r.market_cap is not None else "N/A"
                pe = f"{r.pe_ttm:.1f}" if r.pe_ttm is not None else "N/A"
                turnover = (
                    f"{r.turnover_20d_avg / 1e4:.0f}万"
                    if r.turnover_20d_avg is not None
                    else "N/A"
                )
                lines.append(
                    f"  {r.code} {r.name[:10]:<10} "
                    f"市值={cap} "
                    f"PE={pe} "
                    f"成交额20d={turnover} "
                    f"规则={r.pass_count}/{r.total_rules}"
                )
            push_feishu("text", content="\n".join(lines), title="A股筛选结果")
            console.print("✅ 飞书推送成功")
        except Exception as e:
            logger.warning(f"⚠️ 飞书推送失败: {e}")

    logger.info("✅ A股筛选完成")
