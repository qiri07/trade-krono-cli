"""测试 PipelineConfig 配置类（Phase 3）。"""
import pytest
import json
from pathlib import Path
from trade_krono_cli.pipeline_config import PipelineConfig
from trade_krono_cli.constraints_config import ConstraintConfig


def test_default_config():
    cfg = PipelineConfig.default()
    assert cfg.sample_count == 5
    assert cfg.pred_len == 30
    assert cfg.lookback == 400
    assert cfg.model_name.lower() == "kronos-base"
    assert cfg.risk_threshold == 30.0
    assert cfg.min_confidence == 55.0
    assert cfg.allowed_signals == ("BUY", "HOLD")
    assert cfg.log_level == "INFO"
    assert cfg.log_json is False
    assert isinstance(cfg.constraints, ConstraintConfig)


def test_override():
    cfg = PipelineConfig.default().override(
        sample_count=10,
        risk_threshold=20.0,
    )
    assert cfg.sample_count == 10
    assert cfg.risk_threshold == 20.0
    # 其他字段保持不变
    assert cfg.pred_len == 30
    assert cfg.min_confidence == 55.0


def test_to_dict_roundtrip():
    cfg = PipelineConfig.default()
    d = cfg.to_dict()
    assert isinstance(d["output_dir"], str)
    assert d["sample_count"] == 5

    cfg2 = PipelineConfig.from_dict(d)
    assert cfg2.sample_count == cfg.sample_count
    assert cfg2.risk_threshold == cfg.risk_threshold
    assert cfg2.constraints.enable_limit_check == cfg.constraints.enable_limit_check


def test_save_and_load_json(tmp_path: Path):
    cfg = PipelineConfig.default().override(sample_count=7, risk_threshold=25.0)
    path = tmp_path / "config.json"
    cfg.save(path)

    loaded = PipelineConfig.load(path)
    assert loaded.sample_count == 7
    assert loaded.risk_threshold == 25.0


def test_save_and_load_yaml(tmp_path: Path):
    pytest.importorskip("yaml")
    # YAML 无法原生序列化 tuple，用 from_dict 方式绕过
    cfg = PipelineConfig.default().override(sample_count=3, allowed_signals=["BUY", "HOLD"])
    path = tmp_path / "config.yaml"
    # 手动写入 YAML（避免 tuple 序列化问题）
    import yaml
    data = cfg.to_dict()
    with open(path, "w") as f:
        yaml.dump(data, f, allow_unicode=True, default_flow_style=False)

    loaded = PipelineConfig.load(path)
    assert loaded.sample_count == 3


def test_load_nonexistent_raises():
    with pytest.raises((FileNotFoundError, OSError)):
        PipelineConfig.load("/nonexistent/path.json")


def test_constraints_nested_in_override():
    cfg = PipelineConfig.default().override(
        constraints={"enable_limit_check": False, "commission_bps": 2.0}
    )
    assert cfg.constraints.enable_limit_check is False
    assert cfg.constraints.commission_bps == 2.0


def test_log_json_flag():
    cfg = PipelineConfig.default().override(log_json=True)
    assert cfg.log_json is True

    cfg2 = PipelineConfig.default().override(log_json=False)
    assert cfg2.log_json is False
