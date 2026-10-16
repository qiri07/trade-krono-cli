"""A 股交易约束配置 — 支持市价/限价、maker/taker 手续费。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=False)
class ConstraintConfig:
    """A 股交易约束参数。

    费用模型：
      - commission_bps   : 基础佣金（bps）
      - slippage_bps     : 滑点（bps），市价单默认值
      - stamp_duty_bps   : 印花税率（bps），仅卖出收取

    Maker/Taker 区分：
      - maker_commission_bps : 挂单（限价未立即成交）佣金，通常更低
      - taker_commission_bps : 吃单（市价/限价立即成交）佣金，通常更高
      - 默认：maker = taker（向后兼容）
    """

    enable_limit_check: bool = True
    sse_limit_pct: float = 10.0
    szse_limit_pct: float = 20.0
    enable_t1: bool = True
    enable_st_filter: bool = True

    # ── 基础费用 ───────────────────────────────────────────────────
    commission_bps: float = 3.0
    slippage_bps: float = 5.0
    stamp_duty_bps: float = 1.0
    adjustflag: str = "1"
    enable_cost_model: bool = True

    # ── Maker/Taker 费用（可选，默认与基础费用相同）─────────────────
    maker_commission_bps: float | None = None
    """挂单（限价未立即成交）佣金，默认与 commission_bps 相同。"""
    taker_commission_bps: float | None = None
    """吃单（市价/限价立即成交）佣金，默认与 commission_bps 相同。"""
    maker_slippage_bps: float | None = None
    """挂单滑点，默认与 slippage_bps 相同。"""
    taker_slippage_bps: float | None = None
    """吃单滑点，默认与 slippage_bps 相同。"""

    def merge(self, **overrides) -> ConstraintConfig:
        current = {k: getattr(self, k) for k in self.__dataclass_fields__}
        current.update(overrides)
        return ConstraintConfig(**current)

    def validate(self) -> list[str]:
        errors: list[str] = []
        for name, val in [
            ("commission_bps", self.commission_bps),
            ("slippage_bps", self.slippage_bps),
            ("stamp_duty_bps", self.stamp_duty_bps),
            ("maker_commission_bps", self.maker_commission_bps or self.commission_bps),
            ("taker_commission_bps", self.taker_commission_bps or self.commission_bps),
        ]:
            if val < 0:
                errors.append(f"{name}={val} 不能为负")
        return errors

    # ── 成本计算 ───────────────────────────────────────────────────

    def _effective_commission(self, order_type: str = "taker") -> float:
        """获取有效佣金率。

        Parameters
        ----------
        order_type : "maker" | "taker"

        Returns
        -------
        float
            有效佣金（bps）
        """
        if order_type == "maker":
            return self.maker_commission_bps or self.commission_bps
        return self.taker_commission_bps or self.commission_bps

    def _effective_slippage(self, order_type: str = "taker") -> float:
        """获取有效滑点。

        Parameters
        ----------
        order_type : "maker" | "taker"

        Returns
        -------
        float
            有效滑点（bps）
        """
        if order_type == "maker":
            return self.maker_slippage_bps or self.slippage_bps
        return self.taker_slippage_bps or self.slippage_bps

    def total_roundtrip_bps(self) -> float:
        """双边总成本（bps）。"""
        buy = self._effective_commission("taker") + self._effective_slippage("taker")
        sell = (
            self._effective_commission("taker")
            + self._effective_slippage("taker")
            + self.stamp_duty_bps
        )
        return buy + sell

    def sell_cost_bps(self) -> float:
        """卖出成本（bps）= 佣金 + 滑点 + 印花税。"""
        return (
            self._effective_commission("taker")
            + self._effective_slippage("taker")
            + self.stamp_duty_bps
        )

    def buy_cost_bps(self) -> float:
        """买入成本（bps）= 佣金 + 滑点。"""
        return self._effective_commission("taker") + self._effective_slippage("taker")

    def buy_cost_bps_maker(self) -> float:
        """买入成本（挂单，bps）= maker 佣金 + maker 滑点。"""
        return self._effective_commission("maker") + self._effective_slippage("maker")

    def apply_cost(self, gross_return_pct: float, order_type: str = "taker") -> float:
        """从毛收益率中扣减交易成本。

        Parameters
        ----------
        gross_return_pct : float
            毛收益率（%）
        order_type : "maker" | "taker"
            订单类型

        Returns
        -------
        float
            净收益率（%）
        """
        if not self.enable_cost_model:
            return gross_return_pct
        cost = self._get_order_cost_bps(order_type)
        return gross_return_pct - cost / 100.0

    def apply_roundtrip_cost(self, gross_return_pct: float, order_type: str = "taker") -> float:
        """双边交易成本扣减。

        Parameters
        ----------
        gross_return_pct : float
        order_type : "maker" | "taker"

        Returns
        -------
        float
        """
        if not self.enable_cost_model:
            return gross_return_pct
        cost = self._get_roundtrip_cost_bps(order_type)
        return gross_return_pct - cost / 100.0

    def _get_order_cost_bps(self, order_type: str = "taker") -> float:
        """获取单边成本（bps）。"""
        if order_type == "maker":
            return self.buy_cost_bps_maker()
        return self.buy_cost_bps()

    def _get_roundtrip_cost_bps(self, order_type: str = "taker") -> float:
        """获取双边成本（bps）。"""
        if order_type == "maker":
            buy = self.buy_cost_bps_maker()
            sell = self.sell_cost_bps()  # 卖出永远是 taker（主动平仓）
            return buy + sell
        return self.total_roundtrip_bps()
