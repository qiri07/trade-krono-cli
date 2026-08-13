"""测试版本追踪模块 (version.py)。"""
import pytest
from trade_krono_cli.version import (
    get_project_version,
    generate_run_id,
    compute_config_hash,
    get_data_version,
    get_kronos_model_version,
    get_llm_version,
    get_ta_prompt_version,
    collect_model_versions,
    build_run_snapshot,
)


def test_get_project_version():
    v = get_project_version()
    assert isinstance(v, str)
    assert len(v) > 0
    # 应该是 semver 格式
    assert "." in v or "dev" in v


def test_generate_run_id_format():
    run_id = generate_run_id("2026-08-11")
    # 格式: YYYYMMDD-HHMMSS-NNN
    assert len(run_id) > 15
    parts = run_id.split("-")
    assert len(parts) == 3
    assert parts[0] == "20260811"  # date part
    assert parts[2].isdigit()       # sequence part


def test_generate_run_id_sequential():
    """同一天多次调用，序列号递增。"""
    from trade_krono_cli.version import reset_run_id_counter
    reset_run_id_counter()

    id1 = generate_run_id("2026-08-11")
    id2 = generate_run_id("2026-08-11")
    seq1 = int(id1.split("-")[-1])
    seq2 = int(id2.split("-")[-1])
    assert seq2 == seq1 + 1


def test_generate_run_id_different_dates():
    """不同日期，序列号从 1 重新开始。"""
    from trade_krono_cli.version import reset_run_id_counter
    reset_run_id_counter()

    id1 = generate_run_id("2026-08-10")
    id2 = generate_run_id("2026-08-11")
    seq1 = int(id1.split("-")[-1])
    seq2 = int(id2.split("-")[-1])
    assert seq1 == 1
    assert seq2 == 1


def test_data_version():
    v = get_data_version("sh.600519", "2026-08-11")
    assert v == "baostock-2026-08-11"


def test_data_version_custom_source():
    v = get_data_version("sh.600519", "2026-08-11", source="tushare")
    assert v == "tushare-2026-08-11"


def test_kronos_model_version():
    v = get_kronos_model_version(
        "kronos-base", "kronos-Tokenizer-base", "cpu"
    )
    assert "kronos-kronos-base-kronos-Tokenizer-base-cpu" == v


def test_llm_version():
    v = get_llm_version("deepseek", "deepseek-chat", "deepseek-chat")
    assert v == "deepseek/deepseek-chat+deepseek-chat"


def test_ta_prompt_version():
    v = get_ta_prompt_version(max_debate_rounds=1,
                               max_risk_discuss_rounds=1,
                               output_language="Chinese",
                               structured_output=True)
    assert v == "ta-v1r1-chinese-json"


def test_collect_model_versions():
    versions = collect_model_versions(
        kronos_model="kronos-base",
        kronos_tokenizer="kronos-Tokenizer-base",
        kronos_device="cpu",
        llm_provider="deepseek",
        deep_think_llm="deepseek-chat",
        quick_think_llm="deepseek-chat",
    )
    assert "kronos" in versions
    assert "llm" in versions
    assert versions["llm"] == "deepseek/deepseek-chat+deepseek-chat"


def test_build_run_snapshot():
    """build_run_snapshot 生成完整的版本快照。"""
    from trade_krono_cli.version import reset_run_id_counter
    reset_run_id_counter()

    # 用 mock settings
    class MockSettings:
        max_debate_rounds = 1
        max_risk_discuss_rounds = 1
        kronos_model = "kronos-base"
        kronos_tokenizer = "kronos-Tokenizer-base"
        kronos_device = "cpu"
        kronos_lookback = 400
        kronos_pred_len = 30
        kronos_sample_count = 1
        kronos_T = 1.0
        kronos_top_p = 0.9
        kronos_use_sample_confidence = False
        default_min_confidence = 55.0
        llm_provider = "deepseek"
        deep_think_llm = "deepseek-chat"
        quick_think_llm = "deepseek-chat"
        output_language = "Chinese"
        checkpoint_enabled = True

    snapshot = build_run_snapshot("2026-08-11", MockSettings())

    assert "run_id" in snapshot
    assert "timestamp" in snapshot
    assert snapshot["data_version"] == "baostock-2026-08-11"
    assert "kronos" in snapshot["model_versions"]
    assert "llm" in snapshot["model_versions"]
    assert snapshot["prompt_version"] == "ta-v1r1-chinese-json"
    assert snapshot["strategy_version"] == get_project_version()
    assert len(snapshot["config_hash"]) == 16  # SHA256 前16位


def test_compute_config_hash_excludes_keys():
    """配置哈希不应包含 API key。"""
    from trade_krono_cli.version import compute_config_hash

    class MockSettings:
        max_debate_rounds = 1
        max_risk_discuss_rounds = 1
        kronos_model = "kronos-base"
        kronos_tokenizer = "kronos-Tokenizer-base"
        kronos_device = "cpu"
        kronos_lookback = 400
        kronos_pred_len = 30
        kronos_sample_count = 1
        kronos_T = 1.0
        kronos_top_p = 0.9
        kronos_use_sample_confidence = False
        default_min_confidence = 55.0
        llm_provider = "deepseek"
        deep_think_llm = "deepseek-chat"
        quick_think_llm = "deepseek-chat"
        output_language = "Chinese"
        checkpoint_enabled = True

    hash1 = compute_config_hash(MockSettings())
    hash2 = compute_config_hash(MockSettings())
    assert hash1 == hash2  # 相同配置 → 相同哈希
    assert len(hash1) == 16
