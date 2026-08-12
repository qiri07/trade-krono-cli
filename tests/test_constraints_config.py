"""测试 ConstraintConfig 数据类。"""
import pytest
from trade_krono_cli.constraints_config import ConstraintConfig


def test_default_config():
    cfg = ConstraintConfig()
    assert cfg.enable_limit_check is True
    assert cfg.enable_t1 is True
    assert cfg.enable_st_filter is True
    assert cfg.enable_cost_model is True
    assert cfg.commission_bps == 3.0
    assert cfg.slippage_bps == 5.0
    assert cfg.stamp_duty_bps == 1.0
    assert cfg.sse_limit_pct == 10.0
    assert cfg.szse_limit_pct == 20.0
    assert cfg.adjustflag == "1"


def test_total_roundtrip_bps():
    cfg = ConstraintConfig()
    # buy: 3+5=8bps, sell: 3+5+1=9bps, total=17bps
    assert cfg.total_roundtrip_bps() == 17.0


def test_buy_cost_bps():
    cfg = ConstraintConfig()
    assert cfg.buy_cost_bps() == 8.0


def test_sell_cost_bps():
    cfg = ConstraintConfig()
    assert cfg.sell_cost_bps() == 9.0


def test_apply_cost_with_model():
    cfg = ConstraintConfig()
    # gross 5%, buy cost 8bps = 0.08%
    net = cfg.apply_cost(5.0)
    assert net == pytest.approx(4.92, abs=0.01)


def test_apply_cost_disabled():
    cfg = ConstraintConfig(enable_cost_model=False)
    net = cfg.apply_cost(5.0)
    assert net == 5.0


def test_apply_roundtrip_cost():
    cfg = ConstraintConfig()
    # gross 5%, roundtrip 17bps = 0.17%
    net = cfg.apply_roundtrip_cost(5.0)
    assert net == pytest.approx(4.83, abs=0.01)


def test_apply_roundtrip_cost_disabled():
    cfg = ConstraintConfig(enable_cost_model=False)
    net = cfg.apply_roundtrip_cost(5.0)
    assert net == 5.0


def test_custom_config():
    cfg = ConstraintConfig(
        commission_bps=2.0,
        slippage_bps=3.0,
        stamp_duty_bps=0.5,
        sse_limit_pct=10.0,
        szse_limit_pct=20.0,
    )
    assert cfg.total_roundtrip_bps() == 2.0 + 3.0 + 2.0 + 3.0 + 0.5  # 10.5


def test_config_frozen_false():
    """ConstraintConfig 不是 frozen，允许运行时修改。"""
    cfg = ConstraintConfig()
    cfg.enable_limit_check = False
    assert cfg.enable_limit_check is False
