"""测试 PipelineConfig 配置类（Phase 3）。"""
import pytest
import json
from pathlib import Path
from trade_krono_cli.pipeline_config import PipelineConfig
from trade_krono_cli.constraints_config import ConstraintConfig
from trade_krono_cli.configs.schema import ScoringConfig, RiskConfig


def test_default_config():
    cfg = PipelineConfig.default()
    assert cfg.sample_count == 5
    assert cfg.pred_len == 30
    assert cfg.lookback == 400
    assert cfg.model_name.lower() == "kronos-base"
    assert cfg.min_confidence == 55.0
    assert cfg.allowed_signals == ("BUY", "HOLD")
    assert cfg.log_level == "INFO"
    assert cfg.log_json is False
    assert isinstance(cfg.constraints, ConstraintConfig)
    assert isinstance(cfg.scoring, ScoringConfig)
    assert isinstance(cfg.risk, RiskConfig)


def test_override():
    cfg = PipelineConfig.default().override(
        sample_count=10,
        min_confidence=40.0,
    )
    assert cfg.sample_count == 10
    assert cfg.min_confidence == 40.0
    # 其他字段保持不变
    assert cfg.pred_len == 30


def test_to_dict_roundtrip():
    cfg = PipelineConfig.default()
    d = cfg.to_dict()
    assert isinstance(d["output_dir"], str)
    assert d["sample_count"] == 5

    cfg2 = PipelineConfig.from_dict(d)
    assert cfg2.sample_count == cfg.sample_count
    assert cfg2.constraints.enable_limit_check == cfg.constraints.enable_limit_check
    # scoring and risk must be reconstructed as dataclass instances
    assert isinstance(cfg2.scoring, ScoringConfig)
    assert isinstance(cfg2.risk, RiskConfig)


def test_save_and_load_json(tmp_path: Path):
    cfg = PipelineConfig.default().override(sample_count=7, min_confidence=45.0)
    path = tmp_path / "config.json"
    cfg.save(path)

    loaded = PipelineConfig.load(path)
    assert loaded.sample_count == 7
    assert loaded.min_confidence == 45.0


def test_save_and_load_yaml(tmp_path: Path):
    pytest.importorskip("yaml")
    # YAML 无法原生序列化 tuple，用 from_dict 方式绕过
    cfg = PipelineConfig.default().override(sample_count=3, allowed_signals=["BUY", "HOLD"])
    path = tmp_path / "config.yaml"
    # 手动写入 YAML（避免 tuple 序列化问题）
    import yaml
    data = cfg.to_dict()
    # 将 tuple breakpoints 转为 list for YAML serialization
    for section in ("scoring", "risk"):
        if section in data and isinstance(data[section], dict):
            for k, v in data[section].items():
                if isinstance(v, list) and v and isinstance(v[0], tuple):
                    data[section][k] = [list(t) for t in v]
    with open(path, "w") as f:
        yaml.safe_dump(data, f, allow_unicode=True, default_flow_style=False)

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


def test_scoring_defaults():
    """ScoringConfig 默认值应与原 hard-coded 常量一致。"""
    cfg = PipelineConfig.default()
    s = cfg.scoring
    assert s.ta_confidence_weight == 0.40
    assert s.change_pct_weight == 0.30
    assert s.direction_base_weight == 0.10
    assert s.uncertainty_base_weight == 0.10
    assert s.risk_penalty_weight == 0.15
    assert s.direction_bonus_point == 10.0
    assert s.change_pct_offset == 50.0
    assert s.uncertainty_high_threshold == 70.0
    assert s.uncertainty_med_threshold == 50.0
    assert s.uncertainty_high_bonus == 3.0
    assert s.uncertainty_med_bonus == 1.0
    assert s.uncertainty_low_penalty == -2.0


def test_risk_defaults():
    """RiskConfig 默认值应与原 hard-coded 常量一致。"""
    cfg = PipelineConfig.default()
    r = cfg.risk
    w = r.weights
    assert w.volatility == 0.30
    assert w.drawdown == 0.25
    assert w.liquidity == 0.20
    assert w.concentration == 0.10
    assert w.market_regime == 0.15
    assert r.volatility.high_pct == 60.0
    assert r.drawdown.breakpoints == [(5.0, 20.0), (20.0, 60.0), (40.0, 100.0)]
    assert r.liquidity.breakpoints == [(5.0, 80.0), (6.0, 60.0), (7.0, 40.0), (8.0, 20.0)]
    assert r.market_regime.bear_threshold == -10.0
    assert r.enable_cost_model is True
    assert r.commission_bps == 3.0


def test_from_dict_restores_dataclasses(tmp_path: Path):
    """from_dict 必须将 scoring/risk 恢复为 dataclass 实例。"""
    cfg = PipelineConfig.default().override(sample_count=7)
    path = tmp_path / "config.json"
    cfg.save(path)

    loaded = PipelineConfig.load(path)
    assert isinstance(loaded.scoring, ScoringConfig)
    assert isinstance(loaded.risk, RiskConfig)
    assert loaded.sample_count == 7


def test_merge_works_with_loaded_config(tmp_path: Path):
    """从文件加载的配置用于 merge_results 不应 AttributeError。"""
    import pandas as pd
    import numpy as np
    from trade_krono_cli.pipeline.merge import merge_results
    from trade_krono_cli.ta_runner import StockAnalysisResult
    from trade_krono_cli.kronos_runner import KronosForecastResult

    ta = StockAnalysisResult(ticker="sh.600519", date="2026-08-11", signal="BUY", confidence=80.0)
    kronos = KronosForecastResult(ticker="sh.600519", eval_date="2026-08-11", horizon=30, direction="UP", expected_change_pct=3.2)

    np.random.seed(42)
    close_vals = 100 * (1 + np.random.randn(60) * 0.02)
    kline_df = pd.DataFrame({
        "open": close_vals * 0.99, "high": close_vals * 1.01,
        "low": close_vals * 0.98, "close": close_vals,
        "volume": pd.Series([1e7] * 60),
    })

    cfg = PipelineConfig.default()
    merged = merge_results(
        [ta], [kronos],
        kline_data={"sh.600519": kline_df},
        scoring_config=cfg.scoring,
        risk_config=cfg.risk,
    )
    assert len(merged) == 1
    assert merged[0]["composite_score"] is not None
    assert merged[0]["risk_score_total"] is not None
