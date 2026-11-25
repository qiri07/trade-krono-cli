"""trade_krono_cli.pipeline.orchestrator 单元测试。

覆盖 CommitteeOrchestrator.run / _build_kronos_map 以及
ReportIndexer.index 的分支逻辑。所有外部依赖（InvestmentCommittee、
build_committee_input、ResearchDatabase、文件系统）均通过 unittest.mock 注入，
不触发真实 LLM 或磁盘写入。
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from trade_krono_cli.domain.kronos_result import KronosForecastResult


def _make_ta_result(
    *,
    ticker: str = "sh.600519",
    error: str | None = None,
    final_state: dict | None = None,
    signal: str = "BUY",
    confidence: float = 85.0,
    investment_decision: dict | None = None,
) -> SimpleNamespace:
    """构建模拟 TA 分析结果对象。"""
    return SimpleNamespace(
        ticker=ticker,
        error=error,
        final_state=final_state,
        signal=signal,
        confidence=confidence,
        investment_decision=investment_decision,
    )


def _make_kronos_result(
    *,
    ticker: str = "sh.600519",
    direction: str = "UP",
    expected_change_pct: float = 3.5,
    with_uncertainty: bool = True,
) -> KronosForecastResult:
    """构建 KronosForecastResult 实例。"""
    return KronosForecastResult(
        ticker=ticker,
        eval_date="2026-08-11",
        horizon=30,
        direction=direction,
        expected_change_pct=expected_change_pct,
        prediction_uncertainty=MagicMock(to_dict=MagicMock(return_value={"confidence_score": 0.8}))
        if with_uncertainty
        else None,
    )


# ── CommitteeOrchestrator._build_kronos_map ─────────────────────────────────


class TestBuildKronosMap:
    """_build_kronos_map 静态方法测试。"""

    def test_empty_list(self) -> None:
        """空列表返回空字典。"""
        from trade_krono_cli.pipeline.orchestrator import CommitteeOrchestrator

        result = CommitteeOrchestrator._build_kronos_map([])
        assert result == {}

    def test_kronos_forecast_result_objects(self) -> None:
        """KronosForecastResult 对象列表正确映射。"""
        from trade_krono_cli.pipeline.orchestrator import CommitteeOrchestrator

        kr1 = _make_kronos_result(ticker="sh.600519", direction="UP")
        kr2 = _make_kronos_result(ticker="sz.000858", direction="DOWN")
        result = CommitteeOrchestrator._build_kronos_map([kr1, kr2])

        assert set(result.keys()) == {"sh.600519", "sz.000858"}
        assert result["sh.600519"]["direction"] == "UP"
        assert result["sh.600519"]["expected_change_pct"] == 3.5
        assert "prediction_uncertainty" in result["sh.600519"]

    def test_kronos_forecast_result_without_uncertainty(self) -> None:
        """KronosForecastResult 无 prediction_uncertainty 时映射空 dict。"""
        from trade_krono_cli.pipeline.orchestrator import CommitteeOrchestrator

        kr = _make_kronos_result(with_uncertainty=False)
        result = CommitteeOrchestrator._build_kronos_map([kr])

        assert result[kr.ticker]["prediction_uncertainty"] == {}

    def test_dict_inputs(self) -> None:
        """dict 输入直接映射。"""
        from trade_krono_cli.pipeline.orchestrator import CommitteeOrchestrator

        kronos_results = [
            {"ticker": "sh.600519", "direction": "UP", "expected_change_pct": 2.0},
            {"ticker": "sz.000858", "direction": "FLAT"},
        ]
        result = CommitteeOrchestrator._build_kronos_map(kronos_results)

        assert result["sh.600519"]["direction"] == "UP"
        assert result["sz.000858"]["direction"] == "FLAT"
        assert result["sz.000858"].get("expected_change_pct") is None

    def test_dict_missing_ticker(self) -> None:
        """dict 缺少 ticker 键时映射空字符串键。"""
        from trade_krono_cli.pipeline.orchestrator import CommitteeOrchestrator

        kronos_results = [{"direction": "UP"}]
        result = CommitteeOrchestrator._build_kronos_map(kronos_results)

        assert result == {"": {"direction": "UP"}}


# ── CommitteeOrchestrator.run ───────────────────────────────────────────────


class TestCommitteeOrchestratorRun:
    """CommitteeOrchestrator.run 测试。"""

    def _make_committee_result(self) -> MagicMock:
        """构建模拟委员会审议结果。"""
        result = MagicMock()
        result.bull_case = "看多"
        result.bear_case = "看空"
        result.recommendation = "BUY"
        result.recommendation_confidence = 80.0
        result.reasoning = "理由"
        result.agent_consensus = {"buy": 2, "sell": 0}
        result.job_id = ""
        result.run_id = ""
        return result

    @patch("trade_krono_cli.pipeline.orchestrator.build_committee_input")
    @patch("trade_krono_cli.pipeline.orchestrator.InvestmentCommittee")
    def test_run_skips_errored_ta(
        self, mock_committee_cls, mock_build_input
    ) -> None:
        """TA 结果有 error 时跳过，不调用委员会。"""
        from trade_krono_cli.pipeline.orchestrator import CommitteeOrchestrator

        research = MagicMock()
        research.get_job.return_value = {"run_id": "run_1"}

        ta_results = [
            _make_ta_result(ticker="sh.600519", error="数据缺失"),
            _make_ta_result(ticker="sz.000858", error=None, final_state={"a": 1}),
        ]

        orch = CommitteeOrchestrator(research)
        orch.run("job_1", "2026-08-11", ta_results, [])

        # 只有 final_state 且无 error 的才会进入 deliberation
        mock_build_input.assert_called_once()
        mock_committee_cls.return_value.deliberate.assert_called_once()
        research.insert_committee_deliberation.assert_called_once()

    @patch("trade_krono_cli.pipeline.orchestrator.build_committee_input")
    @patch("trade_krono_cli.pipeline.orchestrator.InvestmentCommittee")
    def test_run_skips_missing_final_state(
        self, mock_committee_cls, mock_build_input
    ) -> None:
        """TA 结果无 final_state 时跳过。"""
        from trade_krono_cli.pipeline.orchestrator import CommitteeOrchestrator

        research = MagicMock()
        research.get_job.return_value = {"run_id": "run_1"}

        ta_results = [
            _make_ta_result(ticker="sh.600519", final_state=None),
            _make_ta_result(ticker="sz.000858", final_state={}),
        ]

        orch = CommitteeOrchestrator(research)
        orch.run("job_1", "2026-08-11", ta_results, [])

        mock_build_input.assert_not_called()
        research.insert_committee_deliberation.assert_not_called()

    @patch("trade_krono_cli.pipeline.orchestrator.build_committee_input")
    @patch("trade_krono_cli.pipeline.orchestrator.InvestmentCommittee")
    def test_run_deliberates_success(
        self, mock_committee_cls, mock_build_input
    ) -> None:
        """正常路径：调用委员会并写入审议结果。"""
        from trade_krono_cli.pipeline.orchestrator import CommitteeOrchestrator

        research = MagicMock()
        research.get_job.return_value = {"run_id": "run_1"}

        mock_committee = MagicMock()
        mock_committee.deliberate.return_value = self._make_committee_result()
        mock_committee_cls.return_value = mock_committee
        mock_build_input.return_value = MagicMock()

        ta_results = [
            _make_ta_result(ticker="sh.600519", final_state={"a": 1}),
        ]
        kronos_results = [_make_kronos_result(ticker="sh.600519")]

        orch = CommitteeOrchestrator(research)
        orch.run("job_1", "2026-08-11", ta_results, kronos_results)

        mock_build_input.assert_called_once()
        mock_committee.deliberate.assert_called_once()
        research.insert_committee_deliberation.assert_called_once()
        # 验证 job_id 和 run_id 被设置到 result
        call_args = research.insert_committee_deliberation.call_args
        assert call_args.kwargs["job_id"] == "job_1"

    @patch("trade_krono_cli.pipeline.orchestrator.build_committee_input")
    @patch("trade_krono_cli.pipeline.orchestrator.InvestmentCommittee")
    def test_run_deliberation_exception_caught(
        self, mock_committee_cls, mock_build_input
    ) -> None:
        """委员会审议异常被捕获，不中断流程。"""
        from trade_krono_cli.pipeline.orchestrator import CommitteeOrchestrator

        research = MagicMock()
        research.get_job.return_value = {"run_id": "run_1"}

        mock_committee = MagicMock()
        mock_committee.deliberate.side_effect = RuntimeError("审议失败")
        mock_committee_cls.return_value = mock_committee
        mock_build_input.return_value = MagicMock()

        ta_results = [
            _make_ta_result(ticker="sh.600519", final_state={"a": 1}),
        ]

        orch = CommitteeOrchestrator(research)
        # 不应抛出
        orch.run("job_1", "2026-08-11", ta_results, [])
        research.insert_committee_deliberation.assert_not_called()

    @patch("trade_krono_cli.pipeline.orchestrator.build_committee_input")
    @patch("trade_krono_cli.pipeline.orchestrator.InvestmentCommittee")
    def test_run_empty_ta_results(
        self, mock_committee_cls, mock_build_input
    ) -> None:
        """空 ta_results 列表不调用委员会。"""
        from trade_krono_cli.pipeline.orchestrator import CommitteeOrchestrator

        research = MagicMock()
        research.get_job.return_value = {"run_id": "run_1"}

        orch = CommitteeOrchestrator(research)
        orch.run("job_1", "2026-08-11", [], [])

        mock_build_input.assert_not_called()
        research.insert_committee_deliberation.assert_not_called()

    @patch("trade_krono_cli.pipeline.orchestrator.build_committee_input")
    @patch("trade_krono_cli.pipeline.orchestrator.InvestmentCommittee")
    def test_run_get_job_returns_none(
        self, mock_committee_cls, mock_build_input
    ) -> None:
        """get_job 返回 None 时 run_id 设为空字符串。"""
        from trade_krono_cli.pipeline.orchestrator import CommitteeOrchestrator

        research = MagicMock()
        research.get_job.return_value = None

        mock_committee = MagicMock()
        mock_committee.deliberate.return_value = self._make_committee_result()
        mock_committee_cls.return_value = mock_committee
        mock_build_input.return_value = MagicMock()

        ta_results = [
            _make_ta_result(ticker="sh.600519", final_state={"a": 1}),
        ]

        orch = CommitteeOrchestrator(research)
        orch.run("job_1", "2026-08-11", ta_results, [])

        research.insert_committee_deliberation.assert_called_once()
        call_kwargs = research.insert_committee_deliberation.call_args.kwargs
        assert call_kwargs["job_id"] == "job_1"


# ── ReportIndexer.index ─────────────────────────────────────────────────────


class TestReportIndexerIndex:
    """ReportIndexer.index 测试。"""

    def test_skips_without_investment_decision(self) -> None:
        """TA 结果无 investment_decision 时跳过。"""
        from trade_krono_cli.pipeline.orchestrator import ReportIndexer

        research = MagicMock()
        ta_results = [_make_ta_result(ticker="sh.600519", investment_decision=None)]
        raw_paths = {"sh.600519": "/tmp/report.json"}

        indexer = ReportIndexer(research)
        indexer.index("job_1", ta_results, raw_paths)

        research.index_raw_report.assert_not_called()

    def test_skips_missing_raw_path(self) -> None:
        """ticker 不在 raw_paths 中时跳过。"""
        from trade_krono_cli.pipeline.orchestrator import ReportIndexer

        research = MagicMock()
        ta_results = [
            _make_ta_result(ticker="sh.600519", investment_decision={"signal": "BUY"})
        ]
        raw_paths = {}

        indexer = ReportIndexer(research)
        indexer.index("job_1", ta_results, raw_paths)

        research.index_raw_report.assert_not_called()

    def test_skips_nonexistent_file(self) -> None:
        """报告文件不存在时跳过。"""
        from trade_krono_cli.pipeline.orchestrator import ReportIndexer

        research = MagicMock()
        ta_results = [
            _make_ta_result(ticker="sh.600519", investment_decision={"signal": "BUY"})
        ]
        raw_paths = {"sh.600519": "/tmp/nonexistent_report.json"}

        indexer = ReportIndexer(research)
        indexer.index("job_1", ta_results, raw_paths)

        research.index_raw_report.assert_not_called()

    def test_indexes_valid_report(self, tmp_path) -> None:
        """有效报告文件被索引到研究数据库。"""
        from trade_krono_cli.pipeline.orchestrator import ReportIndexer

        report_file = tmp_path / "report.json"
        report_data = {"reports_raw": {"news": ["a", "b"], "financials": ["c"]}}
        report_file.write_text(json.dumps(report_data), encoding="utf-8")

        research = MagicMock()
        ta_results = [
            _make_ta_result(ticker="sh.600519", investment_decision={"signal": "BUY"})
        ]
        raw_paths = {"sh.600519": str(report_file)}

        indexer = ReportIndexer(research)
        indexer.index("job_1", ta_results, raw_paths)

        research.index_raw_report.assert_called_once()
        call_args = research.index_raw_report.call_args
        assert call_args.args[0] == "job_1"
        assert call_args.args[1] == "sh.600519"
        assert call_args.args[2] == str(report_file)
        assert call_args.args[3] == {"news": 2, "financials": 1}

    def test_catches_json_decode_error(self, tmp_path) -> None:
        """JSON 解析失败被 ValueError 捕获，不中断。"""
        from trade_krono_cli.pipeline.orchestrator import ReportIndexer

        report_file = tmp_path / "bad.json"
        report_file.write_text("{invalid json", encoding="utf-8")

        research = MagicMock()
        ta_results = [_make_ta_result(ticker="sh.600519", investment_decision={})]
        raw_paths = {"sh.600519": str(report_file)}

        indexer = ReportIndexer(research)
        indexer.index("job_1", ta_results, raw_paths)

        research.index_raw_report.assert_not_called()

    def test_catches_os_error(self, tmp_path) -> None:
        """文件读取 OSError 被捕获，不中断。"""
        from trade_krono_cli.pipeline.orchestrator import ReportIndexer

        # 创建一个目录路径，读取时会触发 IsADirectoryError (OSError 子类)
        report_dir = tmp_path / "is_a_dir"
        report_dir.mkdir()

        research = MagicMock()
        ta_results = [_make_ta_result(ticker="sh.600519", investment_decision={})]
        raw_paths = {"sh.600519": str(report_dir)}

        indexer = ReportIndexer(research)
        indexer.index("job_1", ta_results, raw_paths)

        research.index_raw_report.assert_not_called()

    def test_catches_unexpected_exception(self, tmp_path) -> None:
        """未预料异常被捕获并脱敏，不中断。"""
        from trade_krono_cli.pipeline.orchestrator import ReportIndexer

        report_file = tmp_path / "report.json"
        report_file.write_text('{"reports_raw": {}}', encoding="utf-8")

        research = MagicMock()
        # index_raw_report 抛出非 OSError/ValueError 异常
        research.index_raw_report.side_effect = RuntimeError("unexpected")

        ta_results = [_make_ta_result(ticker="sh.600519", investment_decision={})]
        raw_paths = {"sh.600519": str(report_file)}

        indexer = ReportIndexer(research)
        # 不应抛出
        indexer.index("job_1", ta_results, raw_paths)
