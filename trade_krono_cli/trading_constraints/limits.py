"""trading_constraints.limits — 涨跌停价格检测。"""

from __future__ import annotations

from trade_krono_cli.constraints_config import ConstraintConfig
from trade_krono_cli.security import validate_ticker
from trade_krono_cli.trading_constraints.types import TradingConstraintResult


def detect_exchange(ticker: str) -> str:
    """从 ticker 识别交易所前缀。

    Returns
    -------
    "sse" (上交所) | "szse" (深交所) | "unknown"

    """
    ticker = validate_ticker(ticker)
    if ticker.startswith("sh."):
        return "sse"
    if ticker.startswith("sz."):
        return "szse"
    if ticker.startswith("bj."):
        return "bse"
    return "unknown"


def compute_limit_prices(
    prev_close: float,
    ticker: str | None = None,
    config: ConstraintConfig | None = None,
) -> tuple[float | None, float | None]:
    """根据前一日收盘价计算今日涨跌停价。

    Parameters
    ----------
    prev_close : 前一日收盘价
    ticker : 股票代码（用于判断涨跌停幅度）
    config : 约束配置

    Returns
    -------
    (limit_up_price, limit_down_price)
      任意一个为 None 表示未启用检测

    """
    if config is None:
        config = ConstraintConfig()
    if not config.enable_limit_check:
        return None, None

    if prev_close <= 0:
        return None, None

    limit_pct = config.sse_limit_pct  # 默认主板
    if ticker:
        _ = detect_exchange(ticker)
        code = ticker.split(".")[-1]
        # 科创板(688)在上证，创业板(300/301)在深证，均用20%
        if code.startswith(("688", "300", "301")):
            limit_pct = config.szse_limit_pct

    limit_up = round(prev_close * (1 + limit_pct / 100.0), 2)
    limit_down = round(prev_close * (1 - limit_pct / 100.0), 2)
    return limit_up, limit_down


def check_limit_status(
    ticker: str,
    current_price: float,
    prev_close: float,
    kline_df=None,
    config: ConstraintConfig | None = None,
) -> TradingConstraintResult:
    """检查当前价格是否触及涨跌停。

    Parameters
    ----------
    ticker : 股票代码
    current_price : 当前价格（当日最高/最低或现价）
    prev_close : 前一日收盘价
    kline_df : K 线 DataFrame（可选，用于历史涨停检测）
    config : 约束配置

    Returns
    -------
    TradingConstraintResult
      - allowed=False + reason="LIMIT_UP"/"LIMIT_DOWN" 表示触及涨跌停
      - limit_up_price / limit_down_price 记录边界值

    """
    if config is None:
        config = ConstraintConfig()

    limit_up, limit_down = compute_limit_prices(prev_close, ticker, config)

    if limit_up is None:
        return TradingConstraintResult(symbol=ticker, allowed=True)

    # 检查是否触及涨停（current_price >= 涨停价）
    if current_price >= limit_up * 0.999:  # 允许 0.1% 浮点误差
        return TradingConstraintResult(
            symbol=ticker,
            allowed=False,
            reason="LIMIT_UP",
            limit_up_price=limit_up,
            limit_down_price=limit_down,
        )

    # 检查是否触及跌停
    if limit_down is not None and current_price <= limit_down * 1.001:
        return TradingConstraintResult(
            symbol=ticker,
            allowed=False,
            reason="LIMIT_DOWN",
            limit_up_price=limit_up,
            limit_down_price=limit_down,
        )

    return TradingConstraintResult(
        symbol=ticker,
        allowed=True,
        limit_up_price=limit_up,
        limit_down_price=limit_down,
    )
