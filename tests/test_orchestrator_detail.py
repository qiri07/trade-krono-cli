"""测试 CommitteeOrchestrator 和 ReportIndexer。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from trade_krono_cli.pipeline.orchestrator import CommitteeOrchestrator, ReportIndexer


class TestCommitteeOrchestrator:
    """委员会审议编排器测试。"""

    @pytest.fixture
    def mock_research(self) -> MagicMock:
        """创建模拟 ResearchDatabase。"""
        research = MagicMock()
        research.get_job.return_value = {"run_id": "run_001"}
        return research

    @pytest.fixture
    def orchestrator(self, mock_research: MagicMock) -> CommitteeOrchestrator:
        return CommitteeOrchestrator(research=mock_research)

    def test_build_kronos_map_with_dict(self, orchestrator: CommitteeOrchestrator) -> None:
        """测试字典格式的 Kronos 结果映射。"""
        kr_dict = {"ticker": "sh.600519", "direction": "DOWN", "expected_change_pct": -1.5}
        result = orchestrator._build_kronos_map([kr_dict])
        assert "sh.600519" in result
        assert result["sh.600519"]["direction"] == "DOWN"

    def test_build_kronos_map_empty(self, orchestrator: CommitteeOrchestrator) -> None:
        """测试空结果列表。"""
        result = orchestrator._build_kronos_map([])
        assert result == {}

    def test_run_skips_ta_with_error(
        self, orchestrator: CommitteeOrchestrator, mock_research: MagicMock
    ) -> None:
        """测试跳过有错误的 TA 结果。"""
        ta = MagicMock()
        ta.ticker = "sh.600519"
        ta.error = "Network timeout"
        ta.final_state = None

        orchestrator.run(job_id="job_001", date="2026-09-01", ta_results=[ta], kronos_results=[])
        # 不应调用 insert_committee_deliberation
        mock_research.insert_committee_deliberation.assert_not_called()

    def test_run_skips_ta_without_final_state(
        self, orchestrator: CommitteeOrchestrator, mock_research: MagicMock
    ) -> None:
        """测试跳过没有 final_state 的 TA 结果。"""
        ta = MagicMock()
        ta.ticker = "sh.600519"
        ta.error = None
        ta.final_state = None  # 没有最终状态

        orchestrator.run(job_id="job_001", date="2026-09-01", ta_results=[ta], kronos_results=[])
        mock_research.insert_committee_deliberation.assert_not_called()


class TestReportIndexer:
    """TA 原始报告索引器测试。"""

    @pytest.fixture
    def mock_research(self) -> MagicMock:
        return MagicMock()

    @pytest.fixture
    def indexer(self, mock_research: MagicMock) -> ReportIndexer:
        return ReportIndexer(research=mock_research)

    def test_index_skips_no_decision(
        self, indexer: ReportIndexer, mock_research: MagicMock
    ) -> None:
        """测试无 investment_decision 的 TA 结果被跳过。"""
        ta = MagicMock()
        ta.ticker = "sh.600519"
        ta.investment_decision = None

        indexer.index(
            job_id="job_001", ta_results=[ta], raw_paths={"sh.600519": "/path/to/report.json"}
        )
        mock_research.index_raw_report.assert_not_called()

    def test_index_skips_missing_path(
        self, indexer: ReportIndexer, mock_research: MagicMock
    ) -> None:
        """测试 raw_paths 中无对应路径时跳过。"""
        ta = MagicMock()
        ta.ticker = "sh.600519"
        ta.investment_decision = MagicMock()

        indexer.index(job_id="job_001", ta_results=[ta], raw_paths={})
        mock_research.index_raw_report.assert_not_called()

    def test_index_handles_missing_file(
        self, indexer: ReportIndexer, mock_research: MagicMock
    ) -> None:
        """测试报告文件不存在时不报错。"""
        ta = MagicMock()
        ta.ticker = "sh.600519"
        ta.investment_decision = MagicMock()

        with patch("trade_krono_cli.pipeline.orchestrator.Path.exists", return_value=False):
            indexer.index(
                job_id="job_001",
                ta_results=[ta],
                raw_paths={"sh.600519": "/nonexistent/path.json"},
            )
        mock_research.index_raw_report.assert_not_called()
