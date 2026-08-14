"""测试报告输出。"""
import json
import pytest
from pathlib import Path
from trade_krono_cli.pipeline.reporter import save_json, save_html, print_table, print_summary


@pytest.fixture
def sample_merged():
    return [
        {
            "rank": 1,
            "ticker": "sh.600519",
            "ta_signal": "BUY",
            "ta_confidence": 80.0,
            "ta_reasoning": "基本面良好",
            "kronos_direction": "UP",
            "kronos_change_pct": 3.2,
            "kronos_last_close": 1780.5,
            "kronos_pred_close": 1837.73,
            "kronos_prediction_uncertainty": {
                "expected_return": 3.2,
                "direction": "UP",
                "direction_score": 0.72,
                "volatility": 12.5,
                "path_dispersion": None,
                "confidence_score": 72.0,
                "sample_count_used": 1,
            },
            "composite_score": 82.1,
            "forecast_dict": {"timestamps": [], "close": []},
        },
        {
            "rank": 2,
            "ticker": "sz.000858",
            "ta_signal": "HOLD",
            "ta_confidence": 55.0,
            "ta_reasoning": "观望",
            "kronos_direction": "DOWN",
            "kronos_change_pct": -1.5,
            "kronos_last_close": 25.3,
            "kronos_pred_close": 24.92,
            "kronos_prediction_uncertainty": {
                "expected_return": -1.5,
                "direction": "DOWN",
                "direction_score": 0.55,
                "volatility": 0.8,
                "path_dispersion": None,
                "confidence_score": 55.0,
                "sample_count_used": 1,
            },
            "composite_score": 45.0,
            "forecast_dict": {"timestamps": [], "close": []},
        },
    ]


def test_save_json(sample_merged, tmp_path):
    output = tmp_path / "results.json"
    path = save_json(sample_merged, str(output))
    assert Path(path).exists()
    with open(path) as f:
        data = json.load(f)
    assert len(data) == 2
    assert data[0]["ticker"] == "sh.600519"


def test_save_html(sample_merged, tmp_path):
    output = tmp_path / "report.html"
    path = save_html(sample_merged, str(output), "2026-08-11")
    assert Path(path).exists()
    content = Path(path).read_text()
    assert "trade-krono-cli" in content
    assert "600519" in content
    assert "000858" in content


def test_print_table(sample_merged, capsys):
    print_table(sample_merged)
    captured = capsys.readouterr()
    # rich table truncates ticker; check for score and confidence which are full-width
    assert "82.1" in captured.out
    assert "72.0" in captured.out   # Kronos confidence


def test_print_summary(sample_merged, capsys):
    print_summary(sample_merged, "2026-08-11")
    captured = capsys.readouterr()
    assert "600519" in captured.out
    assert "最佳推荐" in captured.out
