"""Decimal 金融计算工具 — 替换 float64 精度损失。

职责：
  - 安全地将 float/pd.Series 转换为 Decimal
  - 提供金融计算函数（收益率、手续费、仓位计算）
  - 保持与现有 float 接口的兼容性

设计要点：
  - 所有金额计算使用 Decimal(str(value))，避免 float 精度问题
  - 提供 safe_float() 作为降级方案，兼容测试注入
  - 不引入额外依赖，仅使用 Python 标准库 decimal
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal, getcontext

# 设置 Decimal 精度（28 位，足够金融计算）
getcontext().prec = 28

# 常用精度常量
BPS_PRECISION = Decimal("0.0001")  # 1 bps = 0.01%
PERCENT_PRECISION = Decimal("0.01")  # 百分比精度


def to_decimal(value: float | str | Decimal | None) -> Decimal | None:
    """安全地将值转换为 Decimal。

    Parameters
    ----------
    value : float | str | Decimal | None
        输入值

    Returns
    -------
    Decimal | None
        转换后的 Decimal，输入为 None 时返回 None

    Notes
    -----
    使用 str(value) 避免 float 精度问题（如 0.1 + 0.2 != 0.3）
    """
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    # float → str → Decimal，避免精度损失
    return Decimal(str(value))


def dec_round(value: Decimal, places: int = 2) -> Decimal:
    """对 Decimal 进行四舍五入。

    Parameters
    ----------
    value : Decimal
    places : int
        小数位数

    Returns
    -------
    Decimal
    """
    if value is None:
        return Decimal("0")
    quantize_str = "0." + "0" * places
    return value.quantize(Decimal(quantize_str), rounding=ROUND_HALF_UP)


def calc_return(entry_price: Decimal, exit_price: Decimal) -> Decimal:
    """计算收益率（%），使用 Decimal 避免精度损失。

    Parameters
    ----------
    entry_price : Decimal
        入场价
    exit_price : Decimal
        出场价

    Returns
    -------
    Decimal
        收益率（%），保留 4 位小数
    """
    if entry_price <= 0 or exit_price <= 0:
        return Decimal("0")
    return dec_round((exit_price - entry_price) / entry_price * Decimal("100"), 4)


def calc_commission(amount: Decimal, bps: Decimal) -> Decimal:
    """计算手续费（绝对金额）。

    Parameters
    ----------
    amount : Decimal
        交易金额
    bps : Decimal
        手续费率（bps，基点）

    Returns
    -------
    Decimal
        手续费金额
    """
    if amount <= 0 or bps <= 0:
        return Decimal("0")
    return dec_round(amount * bps / Decimal("10000"), 2)


def calc_net_proceeds(
    shares: int,
    exit_price: Decimal,
    commission_bps: Decimal,
    stamp_duty_bps: Decimal = Decimal("0"),
) -> Decimal:
    """计算平仓净收入。

    Parameters
    ----------
    shares : int
        股数
    exit_price : Decimal
        出场价
    commission_bps : Decimal
        手续费率（bps）
    stamp_duty_bps : Decimal
        印花税率（bps），默认 0（买入不收印花税）

    Returns
    -------
    Decimal
        净收入金额
    """
    gross_proceeds = Decimal(shares) * exit_price
    commission = calc_commission(gross_proceeds, commission_bps)
    stamp_duty = calc_commission(gross_proceeds, stamp_duty_bps)
    return dec_round(gross_proceeds - commission - stamp_duty, 2)


def calc_position_value(shares: int, price: Decimal) -> Decimal:
    """计算持仓市值。

    Parameters
    ----------
    shares : int
    price : Decimal

    Returns
    -------
    Decimal
        市值
    """
    return dec_round(Decimal(shares) * price, 2)


def calc_shares(capital: Decimal, price: Decimal, min_trade_size: int = 100) -> int:
    """计算可买入股数（取整到最小交易单位）。

    Parameters
    ----------
    capital : Decimal
        可用资金
    price : Decimal
        单价
    min_trade_size : int
        最小交易单位（A 股通常 100 股）

    Returns
    -------
    int
        可买股数
    """
    if price <= 0 or capital <= 0:
        return 0
    raw_shares = capital / price
    shares = int(raw_shares // min_trade_size) * min_trade_size
    return max(shares, 0)


def apply_cost_to_return(gross_return_pct: Decimal, cost_bps: Decimal) -> Decimal:
    """从毛收益率中扣减交易成本。

    Parameters
    ----------
    gross_return_pct : Decimal
        毛收益率（%）
    cost_bps : Decimal
        成本（bps）

    Returns
    -------
    Decimal
        净收益率（%）
    """
    if gross_return_pct is None or cost_bps is None:
        return Decimal("0")
    return dec_round(gross_return_pct - cost_bps / Decimal("100"), 4)


def safe_float(value: Decimal | float | None) -> float | None:
    """将 Decimal 安全转换为 float（用于接口兼容）。

    Parameters
    ----------
    value : Decimal | float | None

    Returns
    -------
    float | None
    """
    if value is None:
        return None
    if isinstance(value, float):
        return value
    return float(value)


# 向后兼容别名
decimal_to_float = safe_float
