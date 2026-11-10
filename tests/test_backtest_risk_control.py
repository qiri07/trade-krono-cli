"""测试 backtest_risk_control.py 中的 MaxDrawdownControl。

覆盖：
  - 正常上涨阶段保持 100% 仓位
  - 回撤超过阈值触发空仓
  - 回撤恢复后重新建仓
  - 边界情况：空输入、单点数据
"""

from __future__ import annotations

from trade_krono_cli.backtest_risk_control import MaxDrawdownControl


class TestMaxDrawdownControl:
    """MaxDrawdownControl 单元测试。"""

    def test_normal_uptrend_keeps_full_position(self) -> None:
        """持续上涨时应保持 100% 仓位。"""
        ctrl = MaxDrawdownControl(max_dd_threshold=0.20, recovery_threshold=0.05)
        ctrl.update(1_000_000.0)
        assert ctrl.get_exposure() == 1.0

        ctrl.update(1_100_000.0)  # 上涨 10%
        assert ctrl.get_exposure() == 1.0

        ctrl.update(1_200_000.0)  # 继续上涨
        assert ctrl.get_exposure() == 1.0

    def test_drawdown_triggers_position_reduction(self) -> None:
        """回撤超过阈值时应相应减仓。"""
        ctrl = MaxDrawdownControl(max_dd_threshold=0.20, recovery_threshold=0.05)
        ctrl.update(1_000_000.0)
        ctrl.update(1_000_000.0)  # 峰值

        # 回撤 5%，应保持 100%
        ctrl.update(950_000.0)
        assert ctrl.get_exposure() == 1.0

        # 回撤 12%，应在 10%-15% 区间，线性降至 60%
        ctrl.update(880_000.0)
        exposure = ctrl.get_exposure()
        assert 0.6 < exposure < 1.0, f"期望在 0.6-1.0 之间，实际 {exposure}"

        # 回撤 18%，应在 15%-20% 区间，线性降至 0%
        ctrl.update(820_000.0)
        exposure = ctrl.get_exposure()
        assert 0.0 < exposure < 0.6, f"期望在 0.0-0.6 之间，实际 {exposure}"

        # 回撤 25%，应空仓
        ctrl.update(750_000.0)
        assert ctrl.get_exposure() == 0.0

    def test_recovery_restores_position(self) -> None:
        """回撤恢复后应重新建仓。"""
        ctrl = MaxDrawdownControl(max_dd_threshold=0.20, recovery_threshold=0.05)
        ctrl.update(1_000_000.0)
        ctrl.update(1_000_000.0)  # 峰值

        # 跌入空仓
        ctrl.update(750_000.0)
        assert ctrl.get_exposure() == 0.0
        assert ctrl.state.is_in_cash

        # 恢复到回撤 < 5%
        ctrl.update(960_000.0)  # 从峰值 1M 回撤 4%
        assert ctrl.get_exposure() == 1.0
        assert not ctrl.state.is_in_cash

    def test_empty_input(self) -> None:
        """空输入不应崩溃。"""
        ctrl = MaxDrawdownControl()
        assert ctrl.get_exposure() == 1.0
        assert ctrl.state.drawdown_pct == 0.0

    def test_single_point(self) -> None:
        """单点数据应保持满仓。"""
        ctrl = MaxDrawdownControl()
        ctrl.update(500_000.0)
        assert ctrl.get_exposure() == 1.0
        assert ctrl.state.drawdown_pct == 0.0

    def test_reset(self) -> None:
        """重置应清空状态。"""
        ctrl = MaxDrawdownControl()
        ctrl.update(1_000_000.0)
        ctrl.update(500_000.0)
        assert ctrl.get_exposure() == 0.0

        ctrl.reset(2_000_000.0)
        assert ctrl.get_exposure() == 1.0
        assert ctrl.state.peak_value == 2_000_000.0
        assert len(ctrl._history) == 0

    def test_get_summary(self) -> None:
        """get_summary 应返回完整状态。"""
        ctrl = MaxDrawdownControl(max_dd_threshold=0.20, recovery_threshold=0.05)
        ctrl.update(1_000_000.0)
        ctrl.update(900_000.0)

        summary = ctrl.get_summary()
        assert "peak_value" in summary
        assert "current_value" in summary
        assert "drawdown_pct" in summary
        assert "max_drawdown_pct" in summary
        assert "is_in_cash" in summary
        assert "exposure" in summary
        assert "n_updates" in summary
        assert summary["peak_value"] == 1_000_000.0
        assert summary["current_value"] == 900_000.0
        assert summary["drawdown_pct"] == 0.1
        assert summary["exposure"] == 1.0  # 10% 回撤，仍在 100% 仓位
