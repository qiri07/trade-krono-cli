"""测试 DataProviderFactory — 多源降级路由与边界情况。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from trade_krono_cli.data_providers.base import KlineData, RealtimeQuote, StockMetadata
from trade_krono_cli.data_providers.factory import (
    DataProviderFactory,
    get_data_factory,
    reset_data_factory,
)


class TestDataProviderFactory:
    def test_default_chain(self) -> None:
        factory = DataProviderFactory()
        assert factory.primary == "baostock"
        assert factory.fallbacks == ["akshare", "mootdx", "tushare", "tonghuashun"]
        assert factory.provider_chain == ["baostock", "akshare", "mootdx", "tushare", "tonghuashun"]

    def test_custom_chain(self) -> None:
        factory = DataProviderFactory(primary="akshare", fallbacks=["baostock", "mootdx"])
        assert factory.provider_chain == ["akshare", "baostock", "mootdx"]

    def test_get_provider_unknown(self) -> None:
        factory = DataProviderFactory()
        assert factory.get_provider("unknown_source") is None

    def test_get_provider_registry_caching(self) -> None:
        factory = DataProviderFactory()
        cls1 = factory._get_provider_class("baostock")
        cls2 = factory._get_provider_class("baostock")
        assert cls1 is cls2

    def test_fetch_kline_fallback_chain(self) -> None:
        """主源失败时自动降级到备用源。"""
        factory = DataProviderFactory(primary="akshare", fallbacks=["baostock"])

        mock_kline = KlineData(
            timestamps=[datetime(2026, 8, 1)],
            open=[100.0],
            high=[101.0],
            low=[99.0],
            close=[100.5],
            volume=[1e6],
            amount=[1e8],
        )

        from trade_krono_cli.data_providers.baostock_provider import BaostockProvider

        bs_provider = BaostockProvider()

        with patch.object(factory, "get_provider", side_effect=[None, bs_provider]):
            with patch.object(bs_provider, "fetch_kline", return_value=mock_kline):
                with patch.object(bs_provider, "health_check", return_value=True):
                    result = factory.fetch_kline("sh.600519", "2026-01-01", "2026-08-13")
                    assert result is not None
                    assert result.length == 1

    def test_fetch_quote_fallback(self) -> None:
        factory = DataProviderFactory(primary="mootdx", fallbacks=["akshare"])
        mock_quote = RealtimeQuote(ticker="sh.600519", price=1800.0, source="akshare")

        from trade_krono_cli.data_providers.akshare_provider import AkShareProvider

        ak_provider = AkShareProvider()

        with patch.object(factory, "get_provider", side_effect=[None, ak_provider]):
            with patch.object(ak_provider, "fetch_quote", return_value=mock_quote):
                with patch.object(ak_provider, "health_check", return_value=True):
                    result = factory.fetch_quote("sh.600519")
                    assert result is not None
                    assert result.price == 1800.0

    def test_fetch_metadata_fallback(self) -> None:
        factory = DataProviderFactory(primary="mootdx", fallbacks=["tushare"])
        mock_meta = StockMetadata(ticker="sh.600519", industry="白酒", source="tushare")

        from trade_krono_cli.data_providers.tushare_provider import TushareProvider

        ts_provider = TushareProvider()

        with patch.object(factory, "get_provider", side_effect=[None, ts_provider]):
            with patch.object(ts_provider, "fetch_metadata", return_value=mock_meta):
                with patch.object(ts_provider, "health_check", return_value=True):
                    result = factory.fetch_metadata("sh.600519")
                    assert result is not None
                    assert result.industry == "白酒"

    def test_fetch_merged(self) -> None:
        factory = DataProviderFactory(primary="akshare", fallbacks=["baostock"])
        mock_kline = KlineData(
            timestamps=[datetime(2026, 8, 1)],
            open=[100.0],
            high=[101.0],
            low=[99.0],
            close=[100.5],
            volume=[1e6],
            amount=[1e8],
        )
        mock_meta = StockMetadata(ticker="sh.600519", industry="白酒", source="baostock")

        from trade_krono_cli.data_providers.baostock_provider import BaostockProvider

        bs_provider = BaostockProvider()

        calls = []

        def mock_get_provider(name):
            calls.append(name)
            if name == "akshare":
                return None
            return bs_provider

        with patch.object(factory, "get_provider", side_effect=mock_get_provider):
            with patch.object(bs_provider, "fetch_kline", return_value=mock_kline):
                with patch.object(bs_provider, "fetch_metadata", return_value=mock_meta):
                    with patch.object(bs_provider, "fetch_quote", return_value=None):
                        with patch.object(bs_provider, "health_check", return_value=True):
                            result = factory.fetch_merged("sh.600519", "2026-01-01", "2026-08-13")
                            assert result["kline"] is not None
                            assert result["metadata"] is not None

    def test_available_providers(self) -> None:
        factory = DataProviderFactory()
        available = factory.available_providers()
        assert "baostock" in available

    def test_health_check_all(self) -> None:
        factory = DataProviderFactory(primary="akshare", fallbacks=["baostock"])
        result = factory.health_check_all()
        assert "akshare" in result
        assert "baostock" in result

    def test_reset_cache(self) -> None:
        factory = DataProviderFactory()
        factory.get_provider("baostock")
        assert "baostock" in DataProviderFactory._instance_cache
        factory.reset_cache()
        assert "baostock" not in DataProviderFactory._instance_cache

    def test_get_providers_filters_unavailable(self) -> None:
        factory = DataProviderFactory(primary="baostock", fallbacks=["unknown_src"])
        providers = factory.get_providers(["baostock", "unknown_src"])
        names = [p.name for p in providers]
        assert "baostock" in names
        assert "unknown_src" not in names

    def test_factory_singleton(self) -> None:
        """get_data_factory() 返回同一实例。"""
        reset_data_factory()
        f1 = get_data_factory()
        f2 = get_data_factory()
        assert f1 is f2

    def test_all_providers_fail_returns_none(self) -> None:
        """所有 Provider 均不可用时应返回 None。"""
        factory = DataProviderFactory(primary="akshare", fallbacks=[])
        with patch.object(factory, "get_provider", return_value=None):
            result = factory.fetch_kline("sh.600519", "2026-01-01", "2026-08-13")
            assert result is None


# ═══════════════════════════════════════════════════════
# 边缘情况测试
# ═══════════════════════════════════════════════════════


class TestEdgeCases:
    def test_kline_data_nan_protection(self) -> None:
        """to_dataframe 在空数据时不会崩溃。"""
        kd = KlineData()
        df = kd.to_dataframe()
        assert len(df) == 0

    def test_config_data_provider_validation(self) -> None:
        """无效的 data_provider 值应被校验器拒绝。"""
        from trade_krono_cli.config_validator import validate_settings

        s = SimpleNamespace(
            project_root=Path("/tmp"),
            cache_dir=Path("/tmp/cache"),
            results_dir=Path("/tmp/results"),
            tradingagents_root=Path("/tmp/ta"),
            kronos_root=Path("/tmp/kronos"),
            llm_provider="deepseek",
            deep_think_llm="x",
            quick_think_llm="x",
            backend_url=None,
            max_debate_rounds=1,
            max_risk_discuss_rounds=1,
            checkpoint_enabled=True,
            output_language="Chinese",
            kronos_model="x",
            kronos_tokenizer="x",
            kronos_device="cpu",
            kronos_lookback=400,
            kronos_pred_len=30,
            kronos_sample_count=5,
            kronos_T=1.0,
            kronos_top_p=0.9,
            kronos_use_sample_confidence=False,
            kronos_batch_size=8,
            default_min_confidence=55.0,
            default_allowed_signals=["BUY"],
            filter_market_cap_range="",
            filter_industry_whitelist="",
            filter_industry_blacklist="",
            filter_pe_range="",
            filter_pb_range="",
            filter_max_risk_score="",
            filter_min_volume_ratio="",
            filter_exclude_st=True,
            filter_skip_suspended=True,
            filter_skip_new_stock=True,
            filter_new_stock_min_days=60,
            filter_kline_min_completeness=0.85,
            filter_abnormality_risk_boost_enabled=True,
            baostock_sleep_sec=1.0,
            memory_log_path=Path("/tmp/log.jsonl"),
            data_provider="invalid_source",
            data_fallback="",
            akshare_enabled=True,
            mootdx_enabled=True,
            scoring_strategy="linear",
            risk_boost_strategy="fixed_boost",
            risk_boost_multiplier=1.0,
            risk_boost_diminishing_power=0.5,
            retry_max_attempts=3,
            retry_base_delay=2.0,
            retry_jitter=True,
            retry_rate_limit_backoff=True,
            retry_rate_limit_max_wait=60.0,
            degrade_mode="strict",
            ta_cache_fallback_enabled=False,
            ta_cache_max_age_days=7,
        )
        errors, _warnings = validate_settings(s)
        assert any("DATA_PROVIDER" in e for e in errors)

    def test_config_data_fallback_includes_primary(self) -> None:
        """data_fallback 不能包含 primary。"""
        from trade_krono_cli.config_validator import validate_settings

        s = SimpleNamespace(
            project_root=Path("/tmp"),
            cache_dir=Path("/tmp/cache"),
            results_dir=Path("/tmp/results"),
            tradingagents_root=Path("/tmp/ta"),
            kronos_root=Path("/tmp/kronos"),
            llm_provider="deepseek",
            deep_think_llm="x",
            quick_think_llm="x",
            backend_url=None,
            max_debate_rounds=1,
            max_risk_discuss_rounds=1,
            checkpoint_enabled=True,
            output_language="Chinese",
            kronos_model="x",
            kronos_tokenizer="x",
            kronos_device="cpu",
            kronos_lookback=400,
            kronos_pred_len=30,
            kronos_sample_count=5,
            kronos_T=1.0,
            kronos_top_p=0.9,
            kronos_use_sample_confidence=False,
            kronos_batch_size=8,
            default_min_confidence=55.0,
            default_allowed_signals=["BUY"],
            filter_market_cap_range="",
            filter_industry_whitelist="",
            filter_industry_blacklist="",
            filter_pe_range="",
            filter_pb_range="",
            filter_max_risk_score="",
            filter_min_volume_ratio="",
            filter_exclude_st=True,
            filter_skip_suspended=True,
            filter_skip_new_stock=True,
            filter_new_stock_min_days=60,
            filter_kline_min_completeness=0.85,
            filter_abnormality_risk_boost_enabled=True,
            baostock_sleep_sec=1.0,
            memory_log_path=Path("/tmp/log.jsonl"),
            data_provider="baostock",
            data_fallback="baostock,akshare",
            akshare_enabled=True,
            mootdx_enabled=True,
            scoring_strategy="linear",
            risk_boost_strategy="fixed_boost",
            risk_boost_multiplier=1.0,
            risk_boost_diminishing_power=0.5,
            retry_max_attempts=3,
            retry_base_delay=2.0,
            retry_jitter=True,
            retry_rate_limit_backoff=True,
            retry_rate_limit_max_wait=60.0,
            degrade_mode="strict",
            ta_cache_fallback_enabled=False,
            ta_cache_max_age_days=7,
        )
        errors, _warnings = validate_settings(s)
        assert any("DATA_FALLBACK" in e and "不能包含" in e for e in errors)

    def test_factory_provider_class_unknown(self) -> None:
        """未知 Provider 名称应返回 None。"""
        factory = DataProviderFactory()
        assert factory._get_provider_class("nonexistent") is None

    def test_fetch_kline_empty_result(self) -> None:
        """Provider 返回空 KlineData 时应视为失败并继续降级。"""
        factory = DataProviderFactory(primary="baostock", fallbacks=[])
        from trade_krono_cli.data_providers.baostock_provider import BaostockProvider

        bs = BaostockProvider()
        empty_kline = KlineData()  # 空

        with patch.object(factory, "get_provider", return_value=bs):
            with patch.object(bs, "fetch_kline", return_value=empty_kline):
                with patch.object(bs, "health_check", return_value=True):
                    result = factory.fetch_kline("sh.600519", "2026-01-01", "2026-08-13")
                    assert result is None  # 空结果视为失败
