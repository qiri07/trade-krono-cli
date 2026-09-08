"""测试 trade_krono_cli.adapters.base — 适配器基类定义。"""

from __future__ import annotations

import pytest

from trade_krono_cli.adapters.base import KronosAdapter, TradingAgentsAdapter


class TestTradingAgentsAdapterABC:
    """TradingAgentsAdapter 抽象类测试。"""

    def test_cannot_instantiate_directly(self) -> None:
        """不能直接实例化抽象类。"""
        with pytest.raises(TypeError):
            TradingAgentsAdapter()

    def test_complete_implementation(self) -> None:
        """完整实现可以正常实例化。"""

        class Complete(TradingAgentsAdapter):
            def load(self, settings):
                pass

            def build_config(self, **kwargs):
                return {}

            def run_analysis(self, ticker, config):
                return {}

        instance = Complete()
        assert isinstance(instance, TradingAgentsAdapter)

    def test_has_abstract_methods(self) -> None:
        """验证抽象方法存在。"""
        abstract_methods = getattr(TradingAgentsAdapter, "__abstractmethods__", set())
        assert "load" in abstract_methods
        assert "build_config" in abstract_methods
        assert "run_analysis" in abstract_methods


class TestKronosAdapterABC:
    """KronosAdapter 抽象类测试。"""

    def test_cannot_instantiate_directly(self) -> None:
        """不能直接实例化抽象类。"""
        with pytest.raises(TypeError):
            KronosAdapter()

    def test_complete_implementation(self) -> None:
        """完整实现可以正常实例化。"""

        class Complete(KronosAdapter):
            def load_model(self, settings):
                pass

            def predict(self, df, x_timestamp, y_timestamp, pred_len, T, top_p, sample_count=1):
                return None

            def predict_batch(
                self,
                df_list,
                x_timestamp_list,
                y_timestamp_list,
                pred_len,
                T,
                top_p,
                sample_count=1,
            ):
                return []

        instance = Complete()
        assert isinstance(instance, KronosAdapter)

    def test_has_abstract_methods(self) -> None:
        """验证抽象方法存在。"""
        abstract_methods = getattr(KronosAdapter, "__abstractmethods__", set())
        assert "load_model" in abstract_methods
        assert "predict" in abstract_methods
        assert "predict_batch" in abstract_methods


class TestAdapterMethodSignatures:
    """适配器方法签名验证。"""

    def test_tradingagents_load_signature(self) -> None:
        """TradingAgentsAdapter.load 方法签名。"""
        import inspect

        sig = inspect.signature(TradingAgentsAdapter.load)
        params = list(sig.parameters.keys())
        assert "self" in params
        assert "settings" in params

    def test_tradingagents_build_config_signature(self) -> None:
        """TradingAgentsAdapter.build_config 方法签名。"""
        import inspect

        sig = inspect.signature(TradingAgentsAdapter.build_config)
        params = list(sig.parameters.keys())
        assert "self" in params
        assert "kwargs" in params

    def test_tradingagents_run_analysis_signature(self) -> None:
        """TradingAgentsAdapter.run_analysis 方法签名。"""
        import inspect

        sig = inspect.signature(TradingAgentsAdapter.run_analysis)
        params = list(sig.parameters.keys())
        assert "self" in params
        assert "ticker" in params
        assert "config" in params

    def test_kronos_load_model_signature(self) -> None:
        """KronosAdapter.load_model 方法签名。"""
        import inspect

        sig = inspect.signature(KronosAdapter.load_model)
        params = list(sig.parameters.keys())
        assert "self" in params
        assert "settings" in params

    def test_kronos_predict_signature(self) -> None:
        """KronosAdapter.predict 方法签名。"""
        import inspect

        sig = inspect.signature(KronosAdapter.predict)
        params = list(sig.parameters.keys())
        assert "self" in params
        assert "df" in params
        assert "x_timestamp" in params
        assert "y_timestamp" in params
        assert "pred_len" in params
        assert "T" in params
        assert "top_p" in params
        assert "sample_count" in params

    def test_kronos_predict_batch_signature(self) -> None:
        """KronosAdapter.predict_batch 方法签名。"""
        import inspect

        sig = inspect.signature(KronosAdapter.predict_batch)
        params = list(sig.parameters.keys())
        assert "self" in params
        assert "df_list" in params
        assert "x_timestamp_list" in params
        assert "y_timestamp_list" in params
        assert "pred_len" in params
        assert "T" in params
        assert "top_p" in params
        assert "sample_count" in params
