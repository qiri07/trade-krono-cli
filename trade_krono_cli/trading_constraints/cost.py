"""trading_constraints.cost — 交易成本计算。"""

from __future__ import annotations

from trade_krono_cli.constraints_config import ConstraintConfig


def compute_transaction_cost(
    gross_return_pct: float,
    side: str = "sell",
    config: ConstraintConfig | None = None,
) -> float:
    """计算交易成本对收益的影响。

    Parameters
    ----------
    gross_return_pct : 毛收益率（%）
    side : "buy" | "sell" | "roundtrip"
    config : 约束配置

    Returns
    -------
    净收益率（%）

    """
    if config is None:
        config = ConstraintConfig()

    if side == "buy":
        return config.apply_cost(gross_return_pct)
    if side == "sell":
        # 卖出时扣除卖出成本
        if not config.enable_cost_model:
            return gross_return_pct
        return gross_return_pct - config.sell_cost_bps() / 100.0
    if side == "roundtrip":
        return config.apply_roundtrip_cost(gross_return_pct)
    return gross_return_pct
