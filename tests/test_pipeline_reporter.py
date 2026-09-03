"""Tests for trade_krono_cli.pipeline.reporter.

覆盖 JSON/HTML 报告保存、降级标记、控制台表格输出。
"""

from __future__ import annotations

from pathlib import Path

from trade_krono_cli.pipeline.reporter import (
    _degradation_badge,
    save_html_report,
    save_json_report,
)

# ═══════════════════════════════════════════════════════
#  _degradation_badge
# ═══════════════════════════════════════════════════════


class TestDegradationBadge:
    def test_kronos_degraded(self) -> None:
        html, rich = _degradation_badge("kronos_degraded")
        assert "TA-only" in html
        assert "TA-only" in rich

    def test_ta_cache_fallback(self) -> None:
        html, rich = _degradation_badge("ta_cache_fallback")
        assert "缓存TA" in html
        assert "缓存TA" in rich

    def test_none_no_badge(self) -> None:
        html, rich = _degradation_badge(None)
        assert html == ""
        assert rich == "—"

    def test_unknown_mode_no_badge(self) -> None:
        html, rich = _degradation_badge("unknown_mode")
        assert html == ""
        assert rich == "—"


# ═══════════════════════════════════════════════════════
#  save_json_report
# ═══════════════════════════════════════════════════════


class TestSaveJsonReport:
    def test_basic(self, tmp_path: Path) -> None:
        merged = [
            {
                "ticker": "sh.600519",
                "ta_signal": "BUY",
                "kronos_direction": "UP",
                "ranking_score": 75.0,
                "expected_value": 1.5,
            },
            {
                "ticker": "sz.000858",
                "ta_signal": "HOLD",
                "kronos_direction": "FLAT",
                "ranking_score": 45.0,
                "expected_value": -0.3,
            },
        ]
        path = str(tmp_path / "report.json")
        result_path = save_json_report(merged, path)
        assert result_path == path

        import json

        data = json.loads(Path(path).read_text())
        assert data["project"] == "trade-krono-cli"
        assert data["count"] == 2
        assert len(data["results"]) == 2
        assert data["results"][0]["ticker"] == "sh.600519"

    def test_forecast_dict_truncated(self, tmp_path: Path) -> None:
        merged = [
            {
                "ticker": "sh.600519",
                "forecast_dict": {
                    "timestamps": ["2026-08-" + f"{i:02d}" for i in range(1, 21)],
                    "close": list(range(100, 120)),
                },
            },
        ]
        path = str(tmp_path / "trunc.json")
        save_json_report(merged, path)
        import json

        data = json.loads(Path(path).read_text())
        fd = data["results"][0]["forecast_dict"]
        assert len(fd["timestamps"]) <= 5
        assert len(fd["close"]) <= 5
        assert "截断显示" in fd["note"]

    def test_empty_merged(self, tmp_path: Path) -> None:
        path = str(tmp_path / "empty.json")
        save_json_report([], path)
        import json

        data = json.loads(Path(path).read_text())
        assert data["count"] == 0
        assert data["results"] == []


# ═══════════════════════════════════════════════════════
#  save_html_report
# ═══════════════════════════════════════════════════════


class TestSaveHtmlReport:
    def test_basic_html(self, tmp_path: Path) -> None:
        merged = [
            {
                "ticker": "sh.600519",
                "ta_signal": "BUY",
                "ta_confidence": 80.0,
                "kronos_direction": "UP",
                "kronos_change_pct": 2.5,
                "kronos_last_close": 100.0,
                "kronos_pred_close": 102.5,
                "kronos_prediction_uncertainty": {
                    "confidence_score": 75.0,
                    "path_dispersion": 0.02,
                    "direction_score": 0.8,
                },
                "ranking_score": 78.0,
                "expected_value": 1.5,
                "prob_win": 0.65,
                "risk_adjusted_ev": 0.8,
                "degradation_mode": None,
            },
        ]
        path = str(tmp_path / "report.html")
        result_path = save_html_report(merged, path, "2026-08-11")
        assert result_path == path

        content = Path(path).read_text(encoding="utf-8")
        assert "<!DOCTYPE html>" in content
        assert "sh.600519" in content
        assert "BUY" in content
        assert "2.5" in content
        assert "1.500%" in content

    def test_html_with_degradation_badge(self, tmp_path: Path) -> None:
        merged = [
            {
                "ticker": "sh.600519",
                "ranking_score": 60.0,
                "degradation_mode": "kronos_degraded",
                "ta_signal": "BUY",
                "kronos_change_pct": 0.0,
            },
        ]
        path = str(tmp_path / "deg.html")
        save_html_report(merged, path, "2026-08-11")
        content = Path(path).read_text(encoding="utf-8")
        assert "TA-only" in content

    def test_html_color_by_score(self, tmp_path: Path) -> None:
        """ranking_score >= 70 应绿色，50-70 黄色，< 50 红色。"""
        merged = [
            {
                "ticker": "A",
                "ranking_score": 80.0,
                "ta_signal": "BUY",
                "kronos_change_pct": 3.0,
                "kronos_last_close": None,
                "kronos_pred_close": None,
                "kronos_prediction_uncertainty": {},
                "expected_value": None,
                "prob_win": None,
                "risk_adjusted_ev": None,
                "degradation_mode": None,
            },
            {
                "ticker": "B",
                "ranking_score": 55.0,
                "ta_signal": "HOLD",
                "kronos_change_pct": 1.0,
                "kronos_last_close": None,
                "kronos_pred_close": None,
                "kronos_prediction_uncertainty": {},
                "expected_value": None,
                "prob_win": None,
                "risk_adjusted_ev": None,
                "degradation_mode": None,
            },
            {
                "ticker": "C",
                "ranking_score": 30.0,
                "ta_signal": "SELL",
                "kronos_change_pct": -2.0,
                "kronos_last_close": None,
                "kronos_pred_close": None,
                "kronos_prediction_uncertainty": {},
                "expected_value": None,
                "prob_win": None,
                "risk_adjusted_ev": None,
                "degradation_mode": None,
            },
        ]
        path = str(tmp_path / "colors.html")
        save_html_report(merged, path, "2026-08-11")
        content = Path(path).read_text(encoding="utf-8")
        # 绿色 (#28a745) for A (80)
        # 黄色 (#ffc107) for B (55)
        # 红色 (#dc3545) for C (30)
        assert "#28a745" in content
        assert "#ffc107" in content
        assert "#dc3545" in content

    def test_html_with_overweight_signal(self, tmp_path: Path) -> None:
        merged = [
            {
                "ticker": "sh.600519",
                "ta_signal": "OVERWEIGHT",
                "ranking_score": 85.0,
                "kronos_change_pct": 5.0,
                "kronos_last_close": None,
                "kronos_pred_close": None,
                "kronos_prediction_uncertainty": {},
                "expected_value": None,
                "prob_win": None,
                "risk_adjusted_ev": None,
                "degradation_mode": None,
            },
        ]
        path = str(tmp_path / "ow.html")
        save_html_report(merged, path, "2026-08-11")
        content = Path(path).read_text(encoding="utf-8")
        assert "OVERWEIGHT" in content

    def test_html_empty_merged(self, tmp_path: Path) -> None:
        path = str(tmp_path / "empty.html")
        save_html_report([], path, "2026-08-11")
        content = Path(path).read_text(encoding="utf-8")
        assert "<!DOCTYPE html>" in content
        assert "<tbody>" not in content or "<tr>" not in content
