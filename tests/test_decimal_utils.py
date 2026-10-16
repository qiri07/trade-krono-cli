"""Decimal 金融计算工具测试。"""

from __future__ import annotations

from decimal import Decimal

from trade_krono_cli.utils.decimal_utils import (
    calc_commission,
    calc_net_proceeds,
    calc_position_value,
    calc_return,
    calc_shares,
    dec_round,
    to_decimal,
)


class TestToDecimal:
    """测试 to_decimal 转换。"""

    def test_none(self) -> None:
        assert to_decimal(None) is None

    def test_float(self) -> None:
        result = to_decimal(0.1)
        assert result == Decimal("0.1")

    def test_string(self) -> None:
        result = to_decimal("123.45")
        assert result == Decimal("123.45")

    def test_decimal(self) -> None:
        d = Decimal("100.50")
        result = to_decimal(d)
        assert result == d

    def test_float_precision(self) -> None:
        """验证 float → Decimal 避免精度损失。"""
        # 错误方式：Decimal(0.1) → Decimal('0.1000000000000000055511151231257827021181583404541015625')
        # 正确方式：Decimal(str(0.1)) → Decimal('0.1')
        result = to_decimal(0.1)
        assert result == Decimal("0.1")


class TestCalcReturn:
    """测试收益率计算。"""

    def test_positive_return(self) -> None:
        result = calc_return(Decimal("100"), Decimal("110"))
        assert result == Decimal("10.0000")

    def test_negative_return(self) -> None:
        result = calc_return(Decimal("100"), Decimal("90"))
        assert result == Decimal("-10.0000")

    def test_zero_entry_price(self) -> None:
        result = calc_return(Decimal("0"), Decimal("100"))
        assert result == Decimal("0")

    def test_precision(self) -> None:
        """验证精度不损失。"""
        result = calc_return(Decimal("100.123"), Decimal("100.456"))
        assert result != Decimal("0")
        assert result > 0


class TestCalcCommission:
    """测试手续费计算。"""

    def test_basic(self) -> None:
        result = calc_commission(Decimal("10000"), Decimal("3"))
        assert result == Decimal("3.00")

    def test_zero_amount(self) -> None:
        result = calc_commission(Decimal("0"), Decimal("3"))
        assert result == Decimal("0")

    def test_zero_bps(self) -> None:
        result = calc_commission(Decimal("10000"), Decimal("0"))
        assert result == Decimal("0")


class TestCalcNetProceeds:
    """测试平仓净收入计算。"""

    def test_basic(self) -> None:
        result = calc_net_proceeds(
            shares=100,
            exit_price=Decimal("10.5"),
            commission_bps=Decimal("3"),
            stamp_duty_bps=Decimal("1"),
        )
        # 100 * 10.5 = 1050
        # 佣金：1050 * 3 / 10000 = 0.315 → 0.32（四舍五入）
        # 印花税：1050 * 1 / 10000 = 0.105 → 0.11（四舍五入）
        # 净收入：1050 - 0.32 - 0.11 = 1049.57
        expected = Decimal("1049.57")
        assert result == expected

    def test_no_stamp_duty(self) -> None:
        result = calc_net_proceeds(
            shares=100,
            exit_price=Decimal("10.0"),
            commission_bps=Decimal("3"),
            stamp_duty_bps=Decimal("0"),
        )
        # 100 * 10 = 1000
        # 佣金：1000 * 3 / 10000 = 0.30
        # 净收入：1000 - 0.30 = 999.70
        assert result == Decimal("999.70")


class TestCalcPositionValue:
    """测试持仓市值计算。"""

    def test_basic(self) -> None:
        result = calc_position_value(100, Decimal("10.5"))
        assert result == Decimal("1050.00")

    def test_large_position(self) -> None:
        result = calc_position_value(10000, Decimal("100.123"))
        assert result == Decimal("1001230.00")


class TestCalcShares:
    """测试可买股数计算。"""

    def test_basic(self) -> None:
        result = calc_shares(Decimal("10000"), Decimal("10.5"), min_trade_size=100)
        # 10000 / 10.5 = 952.38... → 900 股（取整到 100 的倍数）
        assert result == 900

    def test_insufficient_funds(self) -> None:
        result = calc_shares(Decimal("50"), Decimal("10.0"), min_trade_size=100)
        assert result == 0

    def test_zero_price(self) -> None:
        result = calc_shares(Decimal("10000"), Decimal("0"), min_trade_size=100)
        assert result == 0


class TestDecRound:
    """测试 Decimal 四舍五入。"""

    def test_basic(self) -> None:
        result = dec_round(Decimal("10.125"), 2)
        assert result == Decimal("10.13")

    def test_down_round(self) -> None:
        result = dec_round(Decimal("10.124"), 2)
        assert result == Decimal("10.12")

    def test_none_input(self) -> None:
        result = dec_round(None, 2)  # type: ignore
        assert result == Decimal("0")
