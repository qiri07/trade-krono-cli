"""Tests for pipeline.post_processing module."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from trade_krono_cli.pipeline.post_processing import (
    apply_metadata_filter,
    merge_and_boost,
    run_committee,
)
from trade_krono_cli.ta_runner import StockAnalysisResult

# ═══════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════


@pytest.fixture
def sample_ta_results() -> list[StockAnalysisResult]:
    """创建示例 TA 分析结果列表。"""
    return [
        StockAnalysisResult(
            ticker="sh.600519",
            date="2026-09-15",
            signal="BUY",
            confidence=85.0,
            error=None,
        ),
        StockAnalysisResult(
            ticker="sh.600000",
            date="2026-09-15",
            signal="HOLD",
            confidence=60.0,
            error=None,
        ),
        StockAnalysisResult(
            ticker="sz.000001",
            date="2026-09-15",
            signal="SELL",
            confidence=70.0,
            error=None,
        ),
        StockAnalysisResult(
            ticker="sh.600007",
            date="2026-09-15",
            signal="BUY",
            confidence=55.0,
            error="LLM timeout",
        ),
    ]


@pytest.fixture
def sample_pipeline_config() -> SimpleNamespace:
    """创建示例 PipelineConfig。"""
    return SimpleNamespace(
        min_confidence=55.0,
        allowed_signals=("BUY", "HOLD"),
        market_cap_range=None,
        industry_whitelist=None,
        industry_blacklist=None,
        pe_range=None,
        pb_range=None,
        max_risk_score=None,
        min_volume_ratio=None,
        min_turnover_rate=None,
        exclude_st=True,
        scoring=SimpleNamespace(),
        risk=SimpleNamespace(),
        risk_boost_strategy=SimpleNamespace(strategy="fixed_boost"),
        abnormality_risk_boost_enabled=True,
        scoring_strategy="linear",
        degrade_mode=False,
    )


@pytest.fixture
def sample_constraint_config() -> SimpleNamespace:
    """创建示例 ConstraintConfig。"""
    return SimpleNamespace(
        enable_limit_check=False,
        sse_limit_pct=9.5,
        szse_limit_pct=9.5,
        enable_t1=True,
        enable_st_filter=True,
        commission_bps=2.5,
        slippage_bps=1.0,
        stamp_duty_bps=1.0,
        adjustflag=2,
        enable_cost_model=False,
    )


# ═══════════════════════════════════════════════════════
# apply_metadata_filter 测试
# ═══════════════════════════════════════════════════════


class TestApplyMetadataFilter:
    """apply_metadata_filter 单元测试。"""

    def test_filters_by_signal(self, sample_ta_results, sample_pipeline_config) -> None:
        """信号过滤：只保留 BUY/HOLD。"""
        with (
            patch(
                "trade_krono_cli.pipeline.post_processing.fetch_realtime_quote",
                return_value={"pe": 25.0, "pb": 3.0},
            ),
            patch("trade_krono_cli.pipeline.post_processing.filter_pool") as mock_filter_pool,
        ):
            mock_filter_pool.return_value = sample_ta_results
            result, quote_data = apply_metadata_filter(
                sample_ta_results, {}, sample_pipeline_config, 55.0, ("BUY", "HOLD")
            )
            # 所有结果都通过了信号过滤（BUY/HOLD/SELL 都在原始列表中）
            # filter_pool 按 min_confidence + allowed_signals 过滤
            assert isinstance(result, list)
            assert isinstance(quote_data, dict)

    def test_empty_input(self, sample_pipeline_config) -> None:
        """空输入应返回空列表。"""
        with patch(
            "trade_krono_cli.pipeline.post_processing.filter_pool",
            return_value=[],
        ):
            result, quote_data = apply_metadata_filter(
                [], {}, sample_pipeline_config, 55.0, ("BUY", "HOLD")
            )
            assert result == []
            assert quote_data == {}

    def test_all_errors_skipped(self, sample_pipeline_config) -> None:
        """所有结果都有 error 时，应全部跳过。"""
        error_results = [
            StockAnalysisResult(ticker="sh.600519", date="2026-09-15", error="timeout"),
            StockAnalysisResult(ticker="sh.600000", date="2026-09-15", error="network"),
        ]
        with patch(
            "trade_krono_cli.pipeline.post_processing.filter_pool",
            return_value=error_results,
        ):
            result, _ = apply_metadata_filter(
                error_results, {}, sample_pipeline_config, 55.0, ("BUY", "HOLD")
            )
            assert result == []

    def test_st_stock_filtered_out(self, sample_pipeline_config) -> None:
        """ST 股票应被过滤掉。"""
        ta_with_st = [
            StockAnalysisResult(
                ticker="sh.999999",
                date="2026-09-15",
                signal="BUY",
                confidence=80.0,
                error=None,
            ),
        ]
        abnormal_flags_map = {
            "sh.999999": MagicMock(flag_names=lambda: ["ST"], severity=0.9),
        }
        with (
            patch(
                "trade_krono_cli.pipeline.post_processing.filter_pool",
                return_value=ta_with_st,
            ),
            patch(
                "trade_krono_cli.pipeline.post_processing.fetch_realtime_quote",
                return_value={"pe": None, "pb": None},
            ),
        ):
            result, _ = apply_metadata_filter(
                ta_with_st, abnormal_flags_map, sample_pipeline_config, 55.0, ("BUY", "HOLD")
            )
            # ST 股票应被过滤
            assert len(result) == 0

    def test_error_result_not_in_output(self, sample_pipeline_config) -> None:
        """有 error 的结果不应出现在输出中。"""
        mixed = [
            StockAnalysisResult(
                ticker="sh.600519", date="2026-09-15", signal="BUY", confidence=80.0, error=None
            ),
            StockAnalysisResult(
                ticker="sh.600000", date="2026-09-15", signal="BUY", confidence=80.0, error="fail"
            ),
        ]
        with (
            patch(
                "trade_krono_cli.pipeline.post_processing.filter_pool",
                return_value=mixed,
            ),
            patch(
                "trade_krono_cli.pipeline.post_processing.fetch_realtime_quote",
                return_value={"pe": 20.0, "pb": 2.0},
            ),
        ):
            result, _ = apply_metadata_filter(
                mixed, {}, sample_pipeline_config, 55.0, ("BUY", "HOLD")
            )
            tickers = [r.ticker for r in result]
            assert "sh.600000" not in tickers


# ═══════════════════════════════════════════════════════
# merge_and_boost 测试
# ═══════════════════════════════════════════════════════


class TestMergeAndBoost:
    """merge_and_boost 单元测试。"""

    def test_basic_merge(
        self, sample_ta_results, sample_pipeline_config, sample_constraint_config
    ) -> None:
        """基本合并流程正常执行。"""
        fake_kronos = [MagicMock(ticker="sh.600519", prediction=1.0)]
        with patch(
            "trade_krono_cli.pipeline.post_processing.merge_results",
            return_value=[{"ticker": "sh.600519", "risk_score_total": 30.0}],
        ):
            result = merge_and_boost(
                sample_ta_results,
                fake_kronos,
                {},
                {},
                sample_constraint_config,
                sample_pipeline_config,
                {},
            )
            assert isinstance(result, list)

    def test_abnormality_risk_boost_applied(
        self, sample_ta_results, sample_pipeline_config, sample_constraint_config
    ) -> None:
        """异常标记应触发风险分上调。"""
        fake_kronos = [MagicMock(ticker="sh.600519", prediction=1.0)]
        abnormal_map = {
            "sh.600519": MagicMock(
                flags=["NEW_STOCK"], flag_names=lambda: ["NEW_STOCK"], severity=0.3
            ),
        }
        with (
            patch(
                "trade_krono_cli.pipeline.post_processing.merge_results",
                return_value=[{"ticker": "sh.600519", "risk_score_total": 30.0}],
            ),
            patch(
                "trade_krono_cli.pipeline.post_processing.apply_abnormality_risk_boost",
                return_value=45.0,
            ),
        ):
            result = merge_and_boost(
                sample_ta_results,
                fake_kronos,
                {},
                {},
                sample_constraint_config,
                sample_pipeline_config,
                abnormal_map,
            )
            assert len(result) == 1
            assert result[0]["risk_score_total"] == 45.0
            assert result[0]["abnormal_flags"] == ["NEW_STOCK"]

    def test_boost_disabled_when_configured(
        self, sample_pipeline_config, sample_constraint_config
    ) -> None:
        """当 abnormality_risk_boost_enabled=False 时，不应上调风险分。"""
        cfg = SimpleNamespace(**vars(sample_pipeline_config))
        cfg.abnormality_risk_boost_enabled = False
        fake_ta = [
            StockAnalysisResult(
                ticker="sh.600519", date="2026-09-15", signal="BUY", confidence=80.0, error=None
            ),
        ]
        fake_kronos = [MagicMock(ticker="sh.600519", prediction=1.0)]
        abnormal_map = {
            "sh.600519": MagicMock(flags=["ST"], flag_names=lambda: ["ST"], severity=0.9),
        }
        with patch(
            "trade_krono_cli.pipeline.post_processing.merge_results",
            return_value=[{"ticker": "sh.600519", "risk_score_total": 30.0}],
        ):
            result = merge_and_boost(
                fake_ta,
                fake_kronos,
                {},
                {},
                sample_constraint_config,
                cfg,
                abnormal_map,
            )
            assert result[0]["risk_score_total"] == 30.0  # 未被上调


# ═══════════════════════════════════════════════════════
# run_committee 测试
# ═══════════════════════════════════════════════════════


class TestRunCommittee:
    """run_committee 单元测试。"""

    def test_calls_orchestrator(self) -> None:
        """应调用 CommitteeOrchestrator.run。"""
        mock_research = MagicMock()
        mock_ta = [MagicMock(ticker="sh.600519")]
        mock_kronos = [MagicMock(ticker="sh.600519")]
        with patch("trade_krono_cli.pipeline.post_processing.CommitteeOrchestrator") as MockOrch:
            run_committee(mock_research, "job-1", "2026-09-15", mock_ta, mock_kronos)
            MockOrch.assert_called_once_with(mock_research)
            MockOrch.return_value.run.assert_called_once_with(
                "job-1", "2026-09-15", mock_ta, mock_kronos
            )

    def test_empty_results(self) -> None:
        """空结果也应正常调用（不报错）。"""
        mock_research = MagicMock()
        with patch("trade_krono_cli.pipeline.post_processing.CommitteeOrchestrator") as MockOrch:
            run_committee(mock_research, "job-1", "2026-09-15", [], [])
            MockOrch.assert_called_once_with(mock_research)
            MockOrch.return_value.run.assert_called_once()
