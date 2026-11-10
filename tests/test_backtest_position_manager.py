"""测试 backtest_position_manager.py 中的 DynamicPositionManager。

覆盖：
  - 基础等权分配（无分数）
  - 基于得分的动态分配
  - 顶部/中间/尾部权重分配
  - 最小仓位过滤
  - 空输入边界
"""

from __future__ import annotations

from trade_krono_cli.backtest_position_manager import DynamicPositionManager


class TestDynamicPositionManager:
    """DynamicPositionManager 单元测试。"""

    def test_empty_input(self) -> None:
        """空输入应返回空字典。"""
        mgr = DynamicPositionManager()
        assert mgr.compute_allocations([], {}, 1.0) == {}

    def test_zero_exposure(self) -> None:
        """exposure 为 0 时应返回空字典。"""
        mgr = DynamicPositionManager()
        assert mgr.compute_allocations(["sh.600519"], {"sh.600519": 1.0}, 0.0) == {}

    def test_equal_weight_no_scores(self) -> None:
        """无分数时应等权分配。"""
        mgr = DynamicPositionManager()
        stocks = ["sh.600519", "sz.000001", "sh.600000"]
        result = mgr.compute_allocations(stocks, {}, 1.0)

        assert len(result) == 3
        for v in result.values():
            assert abs(v - 1.0 / 3) < 1e-6
        assert abs(sum(result.values()) - 1.0) < 1e-6

    def test_score_based_allocation(self) -> None:
        """基于得分的加权分配。"""
        mgr = DynamicPositionManager(top_n=3, top_weight=0.30, mid_weight=0.20, tail_weight=0.10)
        stocks = ["A", "B", "C", "D", "E"]
        scores = {"A": 10.0, "B": 8.0, "C": 6.0, "D": 4.0, "E": 2.0}
        result = mgr.compute_allocations(stocks, scores, 1.0)

        # Top 3 应该获得更高权重
        assert result["A"] > result["D"]
        assert result["B"] > result["E"]
        # 总和应接近 1.0
        assert abs(sum(result.values()) - 1.0) < 1e-6

    def test_exposure_scaling(self) -> None:
        """exposure 应按比例缩放所有分配。"""
        mgr = DynamicPositionManager()
        stocks = ["A", "B", "C"]
        scores = {"A": 1.0, "B": 2.0, "C": 3.0}

        full = mgr.compute_allocations(stocks, scores, 1.0)
        half = mgr.compute_allocations(stocks, scores, 0.5)

        for s in stocks:
            assert abs(half[s] - full[s] * 0.5) < 1e-6

    def test_min_position_filter(self) -> None:
        """低于最小仓位的股票应被过滤。"""
        mgr = DynamicPositionManager(min_position_pct=0.10)
        stocks = ["A", "B", "C", "D", "E", "F", "G", "H", "I", "J"]
        scores = {f"s{i}": float(i) for i in range(10)}
        result = mgr.compute_allocations(stocks, scores, 1.0)

        # 所有保留的股票应满足最小仓位
        for v in result.values():
            assert v >= 0.10 - 1e-6

    def test_get_allocation_summary(self) -> None:
        """get_allocation_summary 应返回正确统计。"""
        mgr = DynamicPositionManager()
        stocks = ["A", "B", "C"]
        scores = {"A": 1.0, "B": 2.0, "C": 3.0}
        result = mgr.compute_allocations(stocks, scores, 1.0)
        summary = mgr.get_allocation_summary(result)

        assert summary["n_stocks"] == 3
        assert abs(summary["total_exposure"] - 1.0) < 1e-6
        assert summary["max_allocation"] > 0
        assert summary["min_allocation"] > 0
        assert summary["avg_allocation"] > 0
