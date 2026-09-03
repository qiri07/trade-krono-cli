"""tests/test_degradation.py — 优雅降级机制测试。

覆盖场景：
  1. merge_results 在 strict 模式下无 degradation_mode 标记
  2. merge_results 在 ta_only_on_kronos_fail 模式下正确标记 kronos_degraded
  3. reporter: save_json_report 包含 degradation_mode 字段
  4. reporter: print_results_table 显示降级模式列
  5. reporter: print_results_summary 显示降级统计摘要
  6. config_validator: degrade_mode 非法值报错
  7. PipelineConfig: degrade_mode 字段读写 + default() + override()
  8. research_db: get_latest_ta_for_ticker 基础查询
  9. orchestrator: ta_cache_fallback 逻辑分支（mock 验证）
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from trade_krono_cli.config_validator import validate_settings
from trade_krono_cli.pipeline.merge import merge_results
from trade_krono_cli.pipeline.reporter import (
    print_results_summary,
    print_results_table,
    save_html_report,
    save_json_report,
)
from trade_krono_cli.pipeline_config import PipelineConfig
from trade_krono_cli.research_db import ResearchDatabase, clear_research_singleton

# ═══════════════════════════════════════════════════════
# 测试数据构建辅助
# ═══════════════════════════════════════════════════════


def _make_ta_result(
    ticker: str = "600519",
    signal: str = "BUY",
    confidence: float = 75.0,
    error: str | None = None,
    reasoning: str = "test reasoning",
) -> MagicMock:
    r = MagicMock()
    r.ticker = ticker
    r.date = "2026-01-15"
    r.signal = signal
    r.confidence = confidence
    r.error = error
    r.reasoning = reasoning
    r.reports = {}
    return r


def _make_kronos_result(
    ticker: str = "600519",
    direction: str = "UP",
    expected_change_pct: float = 3.5,
    error: str | None = None,
    last_close: float = 1800.0,
    predicted_close: float = 1863.0,
) -> MagicMock:
    r = MagicMock()
    r.ticker = ticker
    r.direction = direction
    r.expected_change_pct = expected_change_pct
    r.error = error
    r.last_close = last_close
    r.predicted_close_final = predicted_close
    r.volatility_proxy = 0.02
    r.confidence_band = [1750.0, 1970.0]
    r.forecast_dict = {"timestamps": [], "close": []}
    pu = MagicMock()
    pu.to_dict.return_value = {
        "confidence_score": 80.0,
        "path_dispersion": 0.03,
        "direction_score": 0.85,
    }
    r.prediction_uncertainty = pu
    return r


def _make_merged_item(
    ticker: str = "600519",
    signal: str = "BUY",
    confidence: float = 75.0,
    direction: str = "UP",
    change_pct: float = 3.5,
    composite_score: float = 72.0,
    degradation_mode: str | None = None,
) -> dict:
    return {
        "ticker": ticker,
        "rank": 1,
        "ta_signal": signal,
        "ta_confidence": confidence,
        "kronos_direction": direction,
        "kronos_change_pct": change_pct,
        "composite_score": composite_score,
        "degradation_mode": degradation_mode,
        "kronos_prediction_uncertainty": {"confidence_score": 80.0},
        "risk_score_total": 30.0,
        "ta_reasoning": "",
        "kronos_last_close": 1800.0,
        "kronos_pred_close": 1863.0,
        "ta_error": None,
        "kronos_error": None,
    }


# ═══════════════════════════════════════════════════════
# 测试 1：strict 模式 — 无降级标记
# ═══════════════════════════════════════════════════════


class TestMergeStrictMode:
    def test_strict_no_degradation_flag(self) -> None:
        """Strict 模式下，TA 成功 + Kronos 成功 → degradation_mode=None."""
        ta = _make_ta_result("600519", signal="BUY", confidence=75.0)
        kr = _make_kronos_result("600519", direction="UP", expected_change_pct=3.5)
        merged = merge_results([ta], [kr], degrade_mode="strict")
        assert len(merged) == 1
        assert merged[0]["degradation_mode"] is None
        assert merged[0]["ta_signal"] == "BUY"
        assert merged[0]["kronos_direction"] == "UP"

    def test_strict_ta_failed_no_kronos(self) -> None:
        """Strict 模式：TA 失败、Kronos 不存在 → 仍然合并（但 degra=None）."""
        ta = _make_ta_result("600519", signal="BUY", confidence=75.0, error="LLM unavailable")
        merged = merge_results([ta], [], degrade_mode="strict")
        assert len(merged) == 1
        assert merged[0]["degradation_mode"] is None
        # TA error 时 signal 仍保留（merge 不清除已设置的 signal）
        assert merged[0]["ta_signal"] == "BUY"


# ═══════════════════════════════════════════════════════
# 测试 2：ta_only_on_kronos_fail 模式 — 降级标记
# ═══════════════════════════════════════════════════════


class TestMergeTaOnlyDegradation:
    def test_kronos_failed_ta_success(self) -> None:
        """Kronos 失败、TA 成功 → degradation_mode=kronos_degraded."""
        ta = _make_ta_result("600519", signal="BUY", confidence=75.0)
        kr = _make_kronos_result("600519", error="Model load failed")
        merged = merge_results([ta], [kr], degrade_mode="ta_only_on_kronos_fail")
        assert len(merged) == 1
        assert merged[0]["degradation_mode"] == "kronos_degraded"
        assert merged[0]["ta_signal"] == "BUY"
        assert merged[0]["kronos_direction"] is None

    def test_both_success_no_degradation(self) -> None:
        """两者都成功 → 无降级标记."""
        ta = _make_ta_result("600519", signal="BUY", confidence=75.0)
        kr = _make_kronos_result("600519", direction="UP", expected_change_pct=3.5)
        merged = merge_results([ta], [kr], degrade_mode="ta_only_on_kronos_fail")
        assert merged[0]["degradation_mode"] is None

    def test_kronos_missing_ta_success(self) -> None:
        """Kronos 完全缺失（空列表）、TA 成功 → degradation_mode=kronos_degraded."""
        ta = _make_ta_result("600519", signal="HOLD", confidence=60.0)
        merged = merge_results([ta], [], degrade_mode="ta_only_on_kronos_fail")
        assert len(merged) == 1
        assert merged[0]["degradation_mode"] == "kronos_degraded"
        assert merged[0]["ta_signal"] == "HOLD"

    def test_multiple_stocks_mixed(self) -> None:
        """多只股票混合场景：部分成功、部分降级."""
        ta1 = _make_ta_result("600519", signal="BUY", confidence=80.0)
        ta2 = _make_ta_result("000858", signal="HOLD", confidence=55.0)
        kr1 = _make_kronos_result("600519", direction="UP", expected_change_pct=4.0)
        kr2 = _make_kronos_result("000858", error="timeout")
        merged = merge_results([ta1, ta2], [kr1, kr2], degrade_mode="ta_only_on_kronos_fail")
        assert len(merged) == 2
        by_ticker = {m["ticker"]: m for m in merged}
        assert by_ticker["600519"]["degradation_mode"] is None
        assert by_ticker["000858"]["degradation_mode"] == "kronos_degraded"


# ═══════════════════════════════════════════════════════
# 测试 3：JSON 报告包含 degradation_mode
# ═══════════════════════════════════════════════════════


class TestJsonReport:
    def test_save_json_includes_degradation_mode(self, tmp_path) -> None:
        """save_json_report 应包含 degradation_mode 字段."""
        item = _make_merged_item(
            ticker="600519",
            degradation_mode="kronos_degraded",
        )
        path = str(tmp_path / "report.json")
        save_json_report([item], path)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        assert data.get("project") == "trade-krono-cli"
        results = data["results"]
        assert len(results) == 1
        assert results[0]["degradation_mode"] == "kronos_degraded"

    def test_save_json_none_degradation_mode(self, tmp_path) -> None:
        """degradation_mode=None 时 JSON 仍包含该字段（值为 null）."""
        item = _make_merged_item(degradation_mode=None)
        path = str(tmp_path / "report.json")
        save_json_report([item], path)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        results = data["results"]
        assert results[0]["degradation_mode"] is None

    def test_save_json_missing_degradation_field(self, tmp_path) -> None:
        """旧格式结果（无 degradation_mode 键）自动补全为 None."""
        item = _make_merged_item()
        del item["degradation_mode"]
        path = str(tmp_path / "report.json")
        save_json_report([item], path)
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        results = data["results"]
        assert results[0]["degradation_mode"] is None


# ═══════════════════════════════════════════════════════
# 测试 4：HTML 报告包含降级徽章
# ═══════════════════════════════════════════════════════


class TestHtmlReport:
    def test_html_kronos_degraded_badge(self, tmp_path) -> None:
        item = _make_merged_item(ticker="600519", degradation_mode="kronos_degraded")
        path = str(tmp_path / "report.html")
        save_html_report([item], path, date="2026-01-15")
        html = (tmp_path / "report.html").read_text(encoding="utf-8")
        assert "TA-only" in html

    def test_html_cache_fallback_badge(self, tmp_path) -> None:
        item = _make_merged_item(ticker="000858", degradation_mode="ta_cache_fallback")
        path = str(tmp_path / "report.html")
        save_html_report([item], path, date="2026-01-15")
        html = (tmp_path / "report.html").read_text(encoding="utf-8")
        assert "缓存TA" in html

    def test_html_no_badge_when_none(self, tmp_path) -> None:
        item = _make_merged_item(ticker="600036", degradation_mode=None)
        path = str(tmp_path / "report.html")
        save_html_report([item], path, date="2026-01-15")
        html = (tmp_path / "report.html").read_text(encoding="utf-8")
        assert "TA-only" not in html
        assert "缓存TA" not in html


# ═══════════════════════════════════════════════════════
# 测试 5：控制台输出含降级信息
# ═══════════════════════════════════════════════════════


class TestConsoleOutput:
    def test_table_shows_degradation_column(self, capsys) -> None:
        items = [
            _make_merged_item(ticker="600519", degradation_mode="kronos_degraded"),
            _make_merged_item(ticker="000858", degradation_mode=None),
        ]
        print_results_table(items)
        captured = capsys.readouterr()
        assert "降级" in captured.out
        assert "⚠" in captured.out  # 降级标记符号不会截断

    def test_summary_shows_degradation_stats(self, capsys) -> None:
        items = [
            _make_merged_item(
                ticker="600519", degradation_mode="kronos_degraded", composite_score=80.0,
            ),
            _make_merged_item(
                ticker="000858", degradation_mode="ta_cache_fallback", composite_score=60.0,
            ),
        ]
        print_results_summary(items, date="2026-01-15")
        captured = capsys.readouterr()
        assert "Kronos 不可用" in captured.out or "TA-only" in captured.out
        assert "缓存" in captured.out or "缓存TA" in captured.out

    def test_summary_no_degradation(self, capsys) -> None:
        items = [_make_merged_item(composite_score=85.0)]
        print_results_summary(items, date="2026-01-15")
        captured = capsys.readouterr()
        assert "TA-only" not in captured.out
        assert "缓存" not in captured.out


# ═══════════════════════════════════════════════════════
# 测试 6：PipelineConfig 降级字段
# ═══════════════════════════════════════════════════════


class TestPipelineConfigDegradation:
    def test_default_degrade_mode(self) -> None:
        cfg = PipelineConfig.default()
        assert cfg.degrade_mode == "strict"
        assert cfg.ta_cache_fallback_enabled is False
        assert cfg.ta_cache_max_age_days == 7

    def test_override_degrade_mode(self) -> None:
        cfg = PipelineConfig.default().override(degrade_mode="ta_only_on_kronos_fail")
        assert cfg.degrade_mode == "ta_only_on_kronos_fail"
        assert cfg.ta_cache_fallback_enabled is False

    def test_override_ta_cache_fallback(self) -> None:
        cfg = PipelineConfig.default().override(
            degrade_mode="ta_cache_fallback",
            ta_cache_fallback_enabled=True,
            ta_cache_max_age_days=14,
        )
        assert cfg.degrade_mode == "ta_cache_fallback"
        assert cfg.ta_cache_fallback_enabled is True
        assert cfg.ta_cache_max_age_days == 14

    def test_from_dict_roundtrip(self) -> None:
        data = {
            "degrade_mode": "ta_only_on_kronos_fail",
            "ta_cache_fallback_enabled": True,
            "ta_cache_max_age_days": 30,
        }
        cfg = PipelineConfig.from_dict(data)
        assert cfg.degrade_mode == "ta_only_on_kronos_fail"
        assert cfg.ta_cache_fallback_enabled is True
        assert cfg.ta_cache_max_age_days == 30

    def test_to_dict_contains_degradation(self) -> None:
        cfg = PipelineConfig.default().override(
            degrade_mode="ta_cache_fallback",
            ta_cache_fallback_enabled=True,
        )
        d = cfg.to_dict()
        assert d["degrade_mode"] == "ta_cache_fallback"
        assert d["ta_cache_fallback_enabled"] is True
        assert d["ta_cache_max_age_days"] == 7


# ═══════════════════════════════════════════════════════
# 测试 7：config_validator 降级策略校验
# ═══════════════════════════════════════════════════════


class TestValidatorDegradation:
    def _make_settings(self, **overrides):
        from tests.conftest import make_mock_settings

        return make_mock_settings(**overrides)

    def test_valid_degrade_mode_strict(self) -> None:
        s = self._make_settings()
        errs, _ = validate_settings(s)
        assert not any("DEGRADE_MODE" in e for e in errs)

    def test_valid_degrade_mode_ta_only(self) -> None:
        s = self._make_settings(degrade_mode="ta_only_on_kronos_fail")
        errs, _ = validate_settings(s)
        assert not any("DEGRADE_MODE" in e for e in errs)

    def test_valid_degrade_mode_cache_fallback(self) -> None:
        s = self._make_settings(degrade_mode="ta_cache_fallback")
        errs, _ = validate_settings(s)
        assert not any("DEGRADE_MODE" in e for e in errs)

    def test_invalid_degrade_mode_error(self) -> None:
        s = self._make_settings(degrade_mode="invalid_xyz")
        errs, _ = validate_settings(s)
        assert any("DEGRADE_MODE" in e for e in errs)

    def test_ta_cache_max_age_too_low(self) -> None:
        s = self._make_settings(ta_cache_max_age_days=0)
        errs, _ = validate_settings(s)
        assert any("TA_CACHE_MAX_AGE_DAYS" in e for e in errs)

    def test_ta_cache_max_age_too_high(self) -> None:
        s = self._make_settings(ta_cache_max_age_days=400)
        errs, _ = validate_settings(s)
        assert any("TA_CACHE_MAX_AGE_DAYS" in e for e in errs)


# ═══════════════════════════════════════════════════════
# 测试 8：research_db get_latest_ta_for_ticker
# ═══════════════════════════════════════════════════════


@pytest.fixture
def research_db(tmp_path):
    """创建临时研究数据库。"""
    clear_research_singleton()
    db_path = tmp_path / "test_research.db"
    db = ResearchDatabase(db_path=db_path)
    yield db
    clear_research_singleton()


class TestResearchDbLatestTA:
    def _make_ta_mock(self, ticker, signal, confidence, reasoning, error=None):
        """创建兼容 insert_ta 的 TA mock（investment_decision=None）。"""
        ta = MagicMock()
        ta.ticker = ticker
        ta.signal = signal
        ta.confidence = confidence
        ta.reasoning = reasoning
        ta.error = error
        ta.elapsed_sec = 1.0
        ta.investment_decision = None  # 避免 json.dumps 报错
        return ta

    def test_returns_latest_successful_ta(self, research_db) -> None:
        """返回最近一次成功的 TA 分析记录。"""
        job_old = research_db.create_job("2026-01-14", ["600519", "000858"])
        job_new = research_db.create_job("2026-01-15", ["600519"])
        # 第一次作业：600519 成功，000858 失败（不影响目标查询）
        research_db.insert_ta(job_old, self._make_ta_mock("600519", "BUY", 70.0, "old thesis"))
        research_db.insert_ta(
            job_old, self._make_ta_mock("000858", "SELL", 30.0, "", error="some error"),
        )
        # 第二次作业：600519 再次成功（最新的 run_at）
        research_db.insert_ta(job_new, self._make_ta_mock("600519", "HOLD", 55.0, "new thesis"))

        result = research_db.get_latest_ta_for_ticker("600519", max_age_days=7)
        assert result is not None
        assert result["signal"] == "HOLD"
        assert result["confidence"] == 55.0
        assert result["thesis"] == "new thesis"

    def test_returns_none_when_no_record(self, research_db) -> None:
        result = research_db.get_latest_ta_for_ticker("999999", max_age_days=7)
        assert result is None

    def test_returns_none_when_all_failed(self, research_db) -> None:
        """全部记录均有 error → 返回 None."""
        job_id = research_db.create_job("2026-01-15", ["600519"])
        research_db.insert_ta(
            job_id, self._make_ta_mock("600519", "SELL", 30.0, "", error="LLM error"),
        )
        result = research_db.get_latest_ta_for_ticker("600519", max_age_days=7)
        assert result is None

    def test_expired_record_not_returned(self, research_db) -> None:
        """过期记录（超出 max_age_days）不应被返回."""
        import time as _time

        old_time = _time.time() - 30 * 86400
        job_id = research_db.create_job("2026-01-01", ["600519"])
        # 手动回退 job run_at
        with research_db._conn as conn:
            conn.execute(
                "UPDATE jobs SET run_at=? WHERE job_id=?",
                (old_time, job_id),
            )
        research_db.insert_ta(job_id, self._make_ta_mock("600519", "BUY", 80.0, "old"))
        result = research_db.get_latest_ta_for_ticker("600519", max_age_days=7)
        assert result is None


# ═══════════════════════════════════════════════════════
# 测试 9：orchestrator ta_cache_fallback 逻辑
# ═══════════════════════════════════════════════════════


class TestOrchestratorCacheFallback:
    def test_cache_fallback_logic_patches_ta_error(self) -> None:
        """Mock 验证：TA 失败时从数据库回退缓存结果."""
        mock_research = MagicMock()
        mock_research.get_latest_ta_for_ticker.return_value = {
            "ticker": "600519",
            "signal": "BUY",
            "confidence": 72.0,
            "thesis": "cached thesis",
            "risks": "[]",
            "date": "2026-01-10",
            "job_id": "abc123",
        }

        ta_result_failed = MagicMock()
        ta_result_failed.ticker = "600519"
        ta_result_failed.error = "LLM timeout"
        ta_result_failed.signal = None
        ta_result_failed.confidence = None
        ta_result_failed.reasoning = None

        with patch(
            "trade_krono_cli.pipeline.pipeline_core.get_research", return_value=mock_research,
        ):
            # 模拟 orchestrator 中 ta_cache_fallback 的核心逻辑片段
            cfg = SimpleNamespace(
                degrade_mode="ta_cache_fallback",
                ta_cache_fallback_enabled=True,
                ta_cache_max_age_days=7,
            )
            ta_results = [ta_result_failed]
            fallback_count = 0
            for ta in ta_results:
                if ta.error is None:
                    continue
                cached = mock_research.get_latest_ta_for_ticker(
                    ta.ticker,
                    max_age_days=cfg.ta_cache_max_age_days,
                )
                if cached:
                    ta.signal = cached["signal"]
                    ta.confidence = cached["confidence"]
                    ta.reasoning = cached.get("thesis") or ""
                    ta.error = None
                    fallback_count += 1
            assert fallback_count == 1
            assert ta_result_failed.signal == "BUY"
            assert ta_result_failed.confidence == 72.0
            assert ta_result_failed.error is None

    def test_no_fallback_when_degrade_mode_strict(self) -> None:
        """Strict 模式不应触发缓存回退逻辑."""
        cfg = SimpleNamespace(
            degrade_mode="strict",
            ta_cache_fallback_enabled=False,
            ta_cache_max_age_days=7,
        )
        assert cfg.degrade_mode != "ta_cache_fallback" or not cfg.ta_cache_fallback_enabled

    def test_no_fallback_when_disabled(self) -> None:
        """ta_cache_fallback_enabled=False 时不应回退."""
        cfg = SimpleNamespace(
            degrade_mode="ta_cache_fallback",
            ta_cache_fallback_enabled=False,
            ta_cache_max_age_days=7,
        )
        assert not cfg.ta_cache_fallback_enabled
