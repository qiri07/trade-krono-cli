"""Regression tests for bugs fixed in previous phases."""
import pytest
from unittest.mock import MagicMock, patch


class TestRegressionCacheStale:
    """Phase 1: cache key 包含 sample_count 回归测试。"""

    def test_different_sample_count_different_cache_key(self):
        """不同 sample_count 应产生不同的缓存 key。"""
        from trade_krono_cli.cache import Cache
        from pathlib import Path
        import tempfile, os

        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        try:
            cache = Cache(db_path=Path(path))
            cache.set_kronos("sh.600519", "2026-08-12", 30, {"data": "v1"}, sample_count=1)
            cache.set_kronos("sh.600519", "2026-08-12", 30, {"data": "v2"}, sample_count=5)

            r1 = cache.get_kronos("sh.600519", "2026-08-12", 30, sample_count=1)
            r5 = cache.get_kronos("sh.600519", "2026-08-12", 30, sample_count=5)
            assert r1 is not None and r1["data"] == "v1"
            assert r5 is not None and r5["data"] == "v2"
        finally:
            os.unlink(path)


class TestRegressionT1Constraint:
    """Phase 1: T+1 约束修复回归测试。"""

    def test_t1_blocks_same_day_sell(self):
        """T+1 应阻止当日卖出已买入的股票。"""
        from trade_krono_cli.trading_constraints import T1Tracker, enforce_t1

        tracker = T1Tracker()
        tracker.record_buy("sh.600519", "2026-08-11")
        r = enforce_t1("sh.600519", "2026-08-11", tracker)
        assert r.allowed is False
        assert "T1" in r.reason

    def test_t1_allows_next_day_sell(self):
        """T+1 应在次日允许卖出。"""
        from trade_krono_cli.trading_constraints import T1Tracker, enforce_t1

        tracker = T1Tracker()
        tracker.record_buy("sh.600519", "2026-08-11")
        r = enforce_t1("sh.600519", "2026-08-12", tracker)
        assert r.allowed is True


class TestRegressionLimitPrices:
    """Phase 1: 涨跌停价格计算回归测试。"""

    def test_star_market_uses_20_percent(self):
        """科创板（688）应使用 20% 涨跌停。"""
        from trade_krono_cli.trading_constraints import compute_limit_prices
        from trade_krono_cli.constraints_config import ConstraintConfig

        cfg = ConstraintConfig(enable_limit_check=True)
        up, down = compute_limit_prices(100.0, "sh.688001", config=cfg)
        assert up == 120.0
        assert down == 80.0

    def test_gem_uses_20_percent(self):
        """创业板（300）应使用 20% 涨跌停。"""
        from trade_krono_cli.trading_constraints import compute_limit_prices
        from trade_krono_cli.constraints_config import ConstraintConfig

        cfg = ConstraintConfig(enable_limit_check=True)
        up, down = compute_limit_prices(100.0, "sz.300001", config=cfg)
        assert up == 120.0
        assert down == 80.0


class TestRegressionSampleCountDefault:
    """Phase 2: sample_count 默认值回归测试。"""

    def test_default_is_five(self):
        from trade_krono_cli.config import get_settings
        s = get_settings()
        assert s.kronos_sample_count == 5


class TestRegressionUncertaintyBonus:
    """Phase 2: 不确定性置信度映射回归测试。"""

    def test_high_confidence_bonus(self):
        from trade_krono_cli.pipeline.merge import _uncertainty_confidence_bonus
        from trade_krono_cli.configs.schema import ScoringConfig
        assert _uncertainty_confidence_bonus({"confidence_score": 75.0}, ScoringConfig()) == 3.0

    def test_medium_confidence_bonus(self):
        from trade_krono_cli.pipeline.merge import _uncertainty_confidence_bonus
        from trade_krono_cli.configs.schema import ScoringConfig
        assert _uncertainty_confidence_bonus({"confidence_score": 60.0}, ScoringConfig()) == 1.0

    def test_low_confidence_penalty(self):
        from trade_krono_cli.pipeline.merge import _uncertainty_confidence_bonus
        from trade_krono_cli.configs.schema import ScoringConfig
        assert _uncertainty_confidence_bonus({"confidence_score": 30.0}, ScoringConfig()) == -2.0

    def test_none_returns_zero(self):
        from trade_krono_cli.pipeline.merge import _uncertainty_confidence_bonus
        from trade_krono_cli.configs.schema import ScoringConfig
        assert _uncertainty_confidence_bonus(None, ScoringConfig()) == 0.0


class TestRegressionPipelineConfig:
    """Phase 3: PipelineConfig 回归测试。"""

    def test_to_dict_converts_tuple_to_list(self):
        from trade_krono_cli.pipeline_config import PipelineConfig
        cfg = PipelineConfig()
        d = cfg.to_dict()
        # allowed_signals 是 tuple，应转为 list
        assert isinstance(d["allowed_signals"], list)

    def test_yaml_roundtrip(self, tmp_path):
        import yaml
        from trade_krono_cli.pipeline_config import PipelineConfig

        cfg = PipelineConfig(sample_count=10, min_confidence=60.0)
        path = str(tmp_path / "config.yaml")
        cfg.save(path)
        loaded = PipelineConfig.load(path)
        assert loaded.sample_count == 10
        assert loaded.min_confidence == 60.0


class TestRegressionLoggingJson:
    """Phase 3: JSON 结构化日志回归测试。"""

    def test_json_sink_produces_valid_json(self):
        from trade_krono_cli.logging_config import _JsonLogSink
        import json

        sink = _JsonLogSink()
        # write() 接收格式化后的字符串
        json_str = sink.write('{"time":"2026-08-12T10:00:00","level":"INFO","message":"test"}\n')
        # 验证 records 中有内容
        assert len(sink.records) >= 1
        # 尝试解析最后一条
        last = sink.records[-1]
        parsed = json.loads(last)
        assert parsed["message"] == "test"
        assert parsed["level"] == "INFO"

    def test_setup_logger_creates_sink(self):
        from trade_krono_cli.logging_config import setup_logger
        # setup_logger 返回 None（它直接配置 loguru）
        result = setup_logger(level="DEBUG", json_format=True)
        assert result is None


class TestRegressionModuleError:
    """Phase 3: ModuleError / safe_run 回归测试。"""

    def test_safe_run_success(self):
        from trade_krono_cli.errors import safe_run

        def good_fn(x):
            return x * 2

        result = safe_run(good_fn, 5, module="test")
        assert result.success is True
        assert result.data == 10
        assert result.error is None

    def test_safe_run_catches_exception(self):
        from trade_krono_cli.errors import safe_run

        def bad_fn(x):
            raise ValueError("bad input")

        result = safe_run(bad_fn, "x", module="test")
        assert result.success is False
        assert result.error is not None
        # error 是 ModuleError 实例
        from trade_krono_cli.errors import ModuleError
        assert isinstance(result.error, ModuleError)
        assert result.error.module == "test"

    def test_module_error_context(self):
        from trade_krono_cli.errors import ModuleError

        e = ModuleError(module="kronos", message="OOM", original_exception=RuntimeError("out of memory"))
        assert e.module == "kronos"
        assert "kronos" in str(e)
        assert "OOM" in str(e)
