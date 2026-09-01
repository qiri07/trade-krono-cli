"""预测评估命令 — eval-prediction。"""

from __future__ import annotations

import typer

from trade_krono_cli.cli_commands.core import _load_env


def eval_prediction(
    from_date: str | None = typer.Option(None, "--from", "-f", help="起始分析日期 YYYY-MM-DD"),
    to_date: str | None = typer.Option(None, "--to", "-t", help="截止分析日期 YYYY-MM-DD"),
    tickers: str | None = typer.Option(None, "--tickers", "-i", help="只评估指定股票（逗号分隔）"),
    latest: bool = typer.Option(False, "--latest", "-l", help="查看最新评估结果（不重新计算）"),
    backtest: bool = typer.Option(
        False,
        "--backtest",
        "-b",
        help="运行回测引擎，输出年化收益/夏普/最大回撤等绩效指标",
    ),
    rebal_mode: str = typer.Option(
        "fixed_horizon",
        "--rebal-mode",
        help="调仓模式: fixed_horizon / rebal_weekly / rebal_monthly",
    ),
) -> None:
    """预测评估：验证历史预测的准确性。"""
    _load_env()

    from trade_krono_cli.prediction_eval import run_evaluation

    ticker_list = None
    if tickers:
        ticker_list = [x.strip() for x in tickers.split(",") if x.strip()]

    run_evaluation(
        from_date=from_date,
        to_date=to_date,
        tickers=ticker_list,
        latest=latest,
        backtest=backtest,
        rebal_mode=rebal_mode,
    )
