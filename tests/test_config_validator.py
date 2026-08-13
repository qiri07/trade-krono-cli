"""测试配置校验模块 (config_validator.py)。"""
import pytest
from pathlib import Path
from types import SimpleNamespace

from trade_krono_cli.config_validator import validate_settings, print_validation_report


def _make_settings(**overrides) -> SimpleNamespace:
    """构造最小合法 Settings 对象，用 overrides 覆盖字段。"""
    defaults = SimpleNamespace(
        project_root=Path("/tmp/test-project"),
        cache_dir=Path("/tmp/test-project/outputs/cache"),
        results_dir=Path("/tmp/test-project/outputs/results"),
        tradingagents_root=Path("/tmp/test-project/external/TradingAgents-astock"),
        kronos_root=Path("/tmp/test-project/external/Kronos"),
        llm_provider="deepseek",
        deep_think_llm="deepseek-chat",
        quick_think_llm="deepseek-chat",
        backend_url=None,
        max_debate_rounds=1,
        max_risk_discuss_rounds=1,
        checkpoint_enabled=True,
        output_language="Chinese",
        kronos_model="kronos-base",
        kronos_tokenizer="kronos-Tokenizer-base",
        kronos_device="cpu",
        kronos_lookback=400,
        kronos_pred_len=30,
        kronos_sample_count=5,
        kronos_T=1.0,
        kronos_top_p=0.9,
        kronos_use_sample_confidence=False,
        default_min_confidence=55.0,
        default_allowed_signals=["BUY", "HOLD"],
        baostock_sleep_sec=1.0,
        memory_log_path=Path("/tmp/test-project/outputs/memory_log.jsonl"),
    )
    for k, v in overrides.items():
        setattr(defaults, k, v)
    return defaults


# ── 默认配置应通过校验 ────────────────────────────────────────────────────────

def test_default_settings_pass():
    """使用默认值构造的 Settings 不应产生错误。"""
    s = _make_settings()
    errors, warnings = validate_settings(s)
    assert errors == [], f" Unexpected errors: {errors}"


# ── 各字段的错误校验 ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("field,value,error_sub", [
    ("kronos_lookback",  5,    "kronos_lookback"),
    ("kronos_pred_len",  0,    "kronos_pred_len"),
    ("kronos_sample_count", 0, "kronos_sample_count"),
    ("max_debate_rounds",  0,  "max_debate_rounds"),
    ("max_risk_discuss_rounds", 0, "max_risk_discuss_rounds"),
    ("baostock_sleep_sec", -1.0, "baostock_sleep_sec"),
    ("kronos_T",           0.0, "kronos_T"),
    ("default_min_confidence", -1.0, "default_min_confidence"),
    ("default_min_confidence", 101.0, "default_min_confidence"),
    ("kronos_top_p",       0.0, "kronos_top_p"),
    ("kronos_top_p",       1.5, "kronos_top_p"),
    ("llm_provider",       "",  "llm_provider"),
    ("output_language",    "",  "output_language"),
    ("kronos_model",       "",  "kronos_model"),
    ("default_allowed_signals", [], "default_allowed_signals"),
])
def test_validation_errors(field, value, error_sub):
    """各非法字段应产生对应的错误消息。"""
    s = _make_settings(**{field: value})
    errors, warnings = validate_settings(s)
    assert any(error_sub in e for e in errors), (
        f"Expected error containing '{error_sub}', got: {errors}"
    )


# ── 警告项 ────────────────────────────────────────────────────────────────────

def test_warning_for_missing_external_dir(tmp_path):
    """外部依赖目录不存在时应产生警告而非错误。"""
    s = _make_settings(
        tradingagents_root=tmp_path / "nonexistent_ta",
        kronos_root=tmp_path / "nonexistent_kronos",
    )
    errors, warnings = validate_settings(s)
    assert any("TradingAgents" in w for w in warnings)
    assert any("Kronos" in w for w in warnings)
    assert errors == []


def test_warning_for_missing_api_key(monkeypatch):
    """未设置 API Key 时产生警告（非错误）。"""
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    s = _make_settings(llm_provider="deepseek")
    errors, warnings = validate_settings(s)
    assert any("DEEPSEEK_API_KEY" in w for w in warnings)
    assert errors == []


# ── print_validation_report ──────────────────────────────────────────────────

def test_print_validation_report_no_errors():
    """无错误时应返回 True。"""
    assert print_validation_report([], []) is True
    assert print_validation_report([], ["some warning"]) is True


def test_print_validation_report_with_errors(capsys):
    """含错误时应返回 False 并打印消息。"""
    assert print_validation_report(["config error"], ["some warning"]) is False
    captured = capsys.readouterr()
    assert "❌ config error" in captured.out
    assert "⚠️  some warning" in captured.out


def test_print_validation_report_warnings_only(capsys):
    """仅警告时也应打印警告消息并返回 True。"""
    assert print_validation_report([], ["missing directory"]) is True
    captured = capsys.readouterr()
    assert "missing directory" in captured.out
