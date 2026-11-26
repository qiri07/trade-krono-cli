"""测试 DataProviderFactory — 多源降级路由与边界情况。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from trade_krono_cli.data_providers.baostock_provider import BaostockProvider
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
        assert factory.fallbacks == [
            "akshare",
            "tencent",
            "mootdx",
            "tushare",
            "tonghuashun",
            "efinance",
            "easyquotation",
            "adk",
        ]
        assert factory.provider_chain == [
            "baostock",
            "akshare",
            "tencent",
            "mootdx",
            "tushare",
            "tonghuashun",
            "efinance",
            "easyquotation",
            "adk",
        ]

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

        with (
            patch.object(factory, "get_provider", side_effect=[None, bs_provider]),
            patch.object(bs_provider, "fetch_kline", return_value=mock_kline),
            patch.object(bs_provider, "health_check", return_value=True),
        ):
            result = factory.fetch_kline("sh.600519", "2026-01-01", "2026-08-13")
            assert result is not None
            assert result.length == 1

    def test_fetch_quote_fallback(self) -> None:
        factory = DataProviderFactory(primary="mootdx", fallbacks=["akshare"])
        mock_quote = RealtimeQuote(ticker="sh.600519", price=1800.0, source="akshare")

        from trade_krono_cli.data_providers.akshare_provider import AkShareProvider

        ak_provider = AkShareProvider()

        with (
            patch.object(factory, "get_provider", side_effect=[None, ak_provider]),
            patch.object(ak_provider, "fetch_quote", return_value=mock_quote),
            patch.object(ak_provider, "health_check", return_value=True),
        ):
            result = factory.fetch_quote("sh.600519")
            assert result is not None
            assert result.price == 1800.0

    def test_fetch_metadata_fallback(self) -> None:
        factory = DataProviderFactory(primary="mootdx", fallbacks=["tushare"])
        mock_meta = StockMetadata(ticker="sh.600519", industry="白酒", source="tushare")

        from trade_krono_cli.data_providers.tushare_provider import TushareProvider

        ts_provider = TushareProvider()

        def mock_get_provider(name):
            if name == "mootdx":
                return None
            return ts_provider

        with (
            patch.object(factory, "get_provider", side_effect=mock_get_provider),
            patch.object(ts_provider, "fetch_metadata", return_value=mock_meta),
            patch.object(ts_provider, "health_check", return_value=True),
        ):
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

        with (
            patch.object(factory, "get_provider", side_effect=mock_get_provider),
            patch.object(bs_provider, "fetch_kline", return_value=mock_kline),
            patch.object(bs_provider, "fetch_metadata", return_value=mock_meta),
            patch.object(bs_provider, "fetch_quote", return_value=None),
            patch.object(bs_provider, "health_check", return_value=True),
        ):
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
            default_min_confidence=30.0,
            default_allowed_signals=["BUY", "OVERWEIGHT", "HOLD", "SELL"],
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
            default_min_confidence=30.0,
            default_allowed_signals=["BUY", "OVERWEIGHT", "HOLD", "SELL"],
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

        with (
            patch.object(factory, "get_provider", return_value=bs),
            patch.object(bs, "fetch_kline", return_value=empty_kline),
            patch.object(bs, "health_check", return_value=True),
        ):
            result = factory.fetch_kline("sh.600519", "2026-01-01", "2026-08-13")
            assert result is None  # 空结果视为失败


# ═══════════════════════════════════════════════════════
# _chain_for_ticker — 特殊 ticker 处理
# ═══════════════════════════════════════════════════════


class TestChainForTicker:
    def test_bj_ticker_puts_tonghuashun_first(self) -> None:
        """北交所 ticker 应把 tonghuashun 置顶。"""
        factory = DataProviderFactory(primary="akshare", fallbacks=["tonghuashun", "baostock"])
        chain = factory._chain_for_ticker("bj.920001")
        assert chain[0] == "tonghuashun"

    def test_hk_ticker_puts_akshare_hk_first(self) -> None:
        """港股 ticker 应把 akshare_hk 置顶。"""
        factory = DataProviderFactory(primary="baostock", fallbacks=["akshare_hk", "akshare"])
        chain = factory._chain_for_ticker("hk.00700")
        assert chain[0] == "akshare_hk"

    def test_chain_excludes_duplicates(self) -> None:
        """primary 重复出现在 fallbacks 时应去重。"""
        factory = DataProviderFactory(primary="baostock", fallbacks=["baostock", "akshare"])
        chain = factory._chain_for_ticker("sh.600519")
        assert chain.count("baostock") == 1

    def test_chain_uses_cached_ranked_chain(self) -> None:
        """已缓存的排名链应被优先使用。"""
        factory = DataProviderFactory(primary="akshare", fallbacks=["baostock"])
        factory._write_ranked_chain("sh", ["baostock", "akshare"])
        chain = factory._chain_for_ticker("sh.600519")
        # 缓存结果中的 baostock 在 base_chain 中，应排在前面
        assert chain[0] == "baostock"

    def test_invalidate_rank_cache(self) -> None:
        """invalidate_rank_cache 应清除指定 ticker 类型的缓存。"""
        factory = DataProviderFactory()
        factory._write_ranked_chain("sh", ["baostock", "akshare"])
        assert "sh" in factory._rank_cache
        factory.invalidate_rank_cache("sh")
        assert "sh" not in factory._rank_cache


# ═══════════════════════════════════════════════════════
# _provider_healthy — 熔断与健康缓存
# ═══════════════════════════════════════════════════════


class TestProviderHealthy:
    def test_healthy_true_when_health_check_passes(self) -> None:
        """health_check 返回 True 时视为健康。"""
        factory = DataProviderFactory()
        from trade_krono_cli.data_providers.baostock_provider import BaostockProvider

        provider = BaostockProvider()
        with patch.object(provider, "health_check", return_value=True):
            assert factory._provider_healthy("baostock", provider) is True

    def test_healthy_false_when_health_check_fails(self) -> None:
        """health_check 返回 False 时视为不健康。"""
        factory = DataProviderFactory()
        from trade_krono_cli.data_providers.baostock_provider import BaostockProvider

        provider = BaostockProvider()
        with patch.object(provider, "health_check", return_value=False):
            assert factory._provider_healthy("baostock", provider) is False

    def test_healthy_false_when_in_cooldown(self) -> None:
        """在冷却期内直接返回 False，不调用 health_check。"""
        factory = DataProviderFactory()
        from trade_krono_cli.data_providers.baostock_provider import BaostockProvider

        provider = BaostockProvider()
        # 手动设置冷却期
        now = __import__("time").monotonic()
        factory._get_circuit()._provider_unavailable_until["baostock"] = now + 300
        with patch.object(provider, "health_check") as mock_hc:
            assert factory._provider_healthy("baostock", provider) is False
            mock_hc.assert_not_called()

    def test_healthy_resets_fail_count_on_success(self) -> None:
        """成功后重置失败计数。"""
        factory = DataProviderFactory()
        from trade_krono_cli.data_providers.baostock_provider import BaostockProvider

        provider = BaostockProvider()
        factory._get_circuit()._provider_fail_count["baostock"] = 5
        with patch.object(provider, "health_check", return_value=True):
            assert factory._provider_healthy("baostock", provider) is True
            assert "baostock" not in factory._get_circuit()._provider_fail_count

    def test_healthy_increments_fail_count_on_failure(self) -> None:
        """失败后增加失败计数。"""
        factory = DataProviderFactory()
        from trade_krono_cli.data_providers.baostock_provider import BaostockProvider

        provider = BaostockProvider()
        with patch.object(provider, "health_check", return_value=False):
            assert factory._provider_healthy("baostock", provider) is False
            assert factory._get_circuit()._provider_fail_count["baostock"] == 1

    def test_healthy_enters_cooldown_after_threshold(self) -> None:
        """连续失败达到阈值后进入冷却期。"""
        factory = DataProviderFactory()
        circuit = factory._get_circuit()
        for _ in range(3):
            circuit.record_provider_fail("baostock")
        assert "baostock" in circuit._provider_unavailable_until


# ═══════════════════════════════════════════════════════
# _record_provider_fail / _record_provider_success
# ═══════════════════════════════════════════════════════


class TestRecordProviderStatus:
    def test_record_provider_fail_increments_count(self) -> None:
        factory = DataProviderFactory()
        factory._record_provider_fail("test_provider")
        assert factory._get_circuit()._provider_fail_count["test_provider"] == 1

    def test_record_provider_fail_enters_cooldown(self) -> None:
        factory = DataProviderFactory()
        for _ in range(3):
            factory._record_provider_fail("test_provider")
        assert "test_provider" in factory._get_circuit()._provider_unavailable_until

    def test_record_provider_success_resets_count(self) -> None:
        factory = DataProviderFactory()
        factory._get_circuit()._provider_fail_count["test_provider"] = 5
        factory._get_circuit()._provider_unavailable_until["test_provider"] = 9999999999.0
        factory._record_provider_success("test_provider")
        assert "test_provider" not in factory._get_circuit()._provider_fail_count
        assert "test_provider" not in factory._get_circuit()._provider_unavailable_until


# ═══════════════════════════════════════════════════════
# _fetch_kline_with_retry — 重试逻辑
# ═══════════════════════════════════════════════════════


class TestFetchKlineWithRetry:
    def test_retry_on_connection_error_then_success(self) -> None:
        """网络错误重试后成功应返回数据。"""
        factory = DataProviderFactory()
        from trade_krono_cli.data_providers.baostock_provider import BaostockProvider

        provider = BaostockProvider()
        mock_kline = KlineData(
            timestamps=[__import__("datetime").datetime(2026, 8, 1)],
            open=[100.0],
            high=[101.0],
            low=[99.0],
            close=[100.5],
            volume=[1e6],
            amount=[1e8],
        )

        call_count = 0

        def flaky_fetch(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("network down")
            return mock_kline

        with (
            patch.object(provider, "fetch_kline", side_effect=flaky_fetch),
            patch.object(factory, "_rate_limiter") as mock_rl,
        ):
            mock_rl.acquire.return_value = 0
            mock_rl.record_success.return_value = None
            result = factory._fetch_kline_with_retry(
                provider, "baostock", "sh.600519", "2026-01-01", "2026-08-13", "d", "1",
                max_retries=3, base_delay=0.0, max_delay=1.0,
            )
            assert result is not None
            assert call_count == 3

    def test_retry_exhausted_returns_none(self) -> None:
        """重试耗尽后返回 None。"""
        factory = DataProviderFactory()
        from trade_krono_cli.data_providers.baostock_provider import BaostockProvider

        provider = BaostockProvider()

        with (
            patch.object(provider, "fetch_kline", side_effect=ConnectionError("always down")),
            patch.object(factory, "_rate_limiter") as mock_rl,
        ):
            mock_rl.acquire.return_value = 0
            result = factory._fetch_kline_with_retry(
                provider, "baostock", "sh.600519", "2026-01-01", "2026-08-13", "d", "1",
                max_retries=2, base_delay=0.0, max_delay=1.0,
            )
            assert result is None

    def test_non_retryable_exception_returns_none_immediately(self) -> None:
        """RuntimeError 等不可重试异常应立即返回 None。"""
        factory = DataProviderFactory()
        from trade_krono_cli.data_providers.baostock_provider import BaostockProvider

        provider = BaostockProvider()

        with (
            patch.object(provider, "fetch_kline", side_effect=RuntimeError("hard failure")),
            patch.object(factory, "_rate_limiter") as mock_rl,
        ):
            mock_rl.acquire.return_value = 0
            result = factory._fetch_kline_with_retry(
                provider, "baostock", "sh.600519", "2026-01-01", "2026-08-13", "d", "1",
                max_retries=3, base_delay=0.0, max_delay=1.0,
            )
            assert result is None

    def test_fetch_kline_from_provider(self) -> None:
        """fetch_kline_from_provider 从指定 Provider 拉取，不做降级。"""
        factory = DataProviderFactory()
        from trade_krono_cli.data_providers.baostock_provider import BaostockProvider

        provider = BaostockProvider()
        mock_kline = KlineData(
            timestamps=[__import__("datetime").datetime(2026, 8, 1)],
            open=[100.0],
            high=[101.0],
            low=[99.0],
            close=[100.5],
            volume=[1e6],
            amount=[1e8],
        )

        with (
            patch.object(factory, "get_provider", return_value=provider),
            patch.object(provider, "fetch_kline", return_value=mock_kline),
            patch.object(provider, "health_check", return_value=True),
            patch.object(factory, "_rate_limiter") as mock_rl,
        ):
            mock_rl.acquire.return_value = 0
            result = factory.fetch_kline_from_provider(
                "baostock", "sh.600519", "2026-01-01", "2026-08-13",
            )
            assert result is not None
            assert result.length == 1

    def test_fetch_kline_from_provider_unavailable(self) -> None:
        """Provider 不可用时 fetch_kline_from_provider 返回 None。"""
        factory = DataProviderFactory()
        with patch.object(factory, "get_provider", return_value=None):
            result = factory.fetch_kline_from_provider(
                "nonexistent", "sh.600519", "2026-01-01", "2026-08-13",
            )
            assert result is None


# ═══════════════════════════════════════════════════════
# _retry_fetch — 通用重试
# ═══════════════════════════════════════════════════════


class TestRetryFetch:
    def test_retry_fetch_success(self) -> None:
        factory = DataProviderFactory()

        def fetch_fn() -> str:
            return "ok"

        with patch.object(factory, "_rate_limiter") as mock_rl:
            mock_rl.acquire.return_value = 0
            result = factory._retry_fetch("test_provider", "sh.600519", fetch_fn, "行情")
            assert result == "ok"

    def test_retry_fetch_connection_error_then_success(self) -> None:
        factory = DataProviderFactory()
        call_count = 0

        def flaky():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ConnectionError("down")
            return "ok"

        with patch.object(factory, "_rate_limiter") as mock_rl:
            mock_rl.acquire.return_value = 0
            result = factory._retry_fetch(
                "test_provider", "sh.600519", flaky, "行情",
                max_retries=3, base_delay=0.0, max_delay=1.0,
            )
            assert result == "ok"
            assert call_count == 2

    def test_retry_fetch_exhausted_returns_none(self) -> None:
        factory = DataProviderFactory()

        def always_fail():
            raise ConnectionError("always down")

        with patch.object(factory, "_rate_limiter") as mock_rl:
            mock_rl.acquire.return_value = 0
            result = factory._retry_fetch(
                "test_provider", "sh.600519", always_fail, "行情",
                max_retries=2, base_delay=0.0, max_delay=1.0,
            )
            assert result is None


# ═══════════════════════════════════════════════════════
# fetch_metadata — 字段合并
# ═══════════════════════════════════════════════════════


class TestFetchMetadataMerge:
    def test_fetch_metadata_merges_fields(self) -> None:
        """多个 Provider 的非空字段应合并。"""
        factory = DataProviderFactory(primary="p1", fallbacks=["p2"])
        from trade_krono_cli.data_providers.base import StockMetadata

        meta1 = StockMetadata(ticker="sh.600519", pe_ttm=10.0, source="p1")
        meta2 = StockMetadata(ticker="sh.600519", pb=3.0, industry="白酒", source="p2")

        p1 = MagicMock()
        p1.supports_metadata = True
        p1.fetch_metadata.return_value = meta1
        p2 = MagicMock()
        p2.supports_metadata = True
        p2.fetch_metadata.return_value = meta2

        with (
            patch.object(factory, "get_provider", side_effect=[p1, p2]),
            patch.object(p1, "health_check", return_value=True),
            patch.object(p2, "health_check", return_value=True),
            patch.object(factory, "_rate_limiter") as mock_rl,
        ):
            mock_rl.acquire.return_value = 0
            result = factory.fetch_metadata("sh.600519")
            assert result is not None
            assert result.pe_ttm == 10.0
            assert result.pb == 3.0
            assert result.industry == "白酒"

    def test_fetch_metadata_none_when_all_fail(self) -> None:
        factory = DataProviderFactory()
        with patch.object(factory, "get_provider", return_value=None):
            result = factory.fetch_metadata("sh.600519")
            assert result is None


# ═══════════════════════════════════════════════════════
# fetch_quotes_batch / fetch_metadata_batch
# ═══════════════════════════════════════════════════════


class TestBatchFetch:
    def test_fetch_quotes_batch_empty(self) -> None:
        factory = DataProviderFactory()
        assert factory.fetch_quotes_batch([]) == {}

    def test_fetch_quotes_batch_success(self) -> None:
        from trade_krono_cli.data_providers.base import RealtimeQuote

        factory = DataProviderFactory(primary="p1")
        q = RealtimeQuote(ticker="sh.600519", price=100.0, source="p1")
        p = MagicMock()
        p.supports_quote = True
        p.fetch_quotes_batch.return_value = {"sh.600519": q}

        with (
            patch.object(factory, "get_provider", return_value=p),
            patch.object(p, "health_check", return_value=True),
            patch.object(factory, "_rate_limiter") as mock_rl,
        ):
            mock_rl.acquire.return_value = 0
            result = factory.fetch_quotes_batch(["sh.600519"])
            assert "sh.600519" in result

    def test_fetch_metadata_batch_empty(self) -> None:
        factory = DataProviderFactory()
        assert factory.fetch_metadata_batch([]) == {}


# ═══════════════════════════════════════════════════════
# bench_all / get_ranked_chain_for_ticker
# ═══════════════════════════════════════════════════════


class TestBenchmark:
    def test_bench_all_no_providers(self) -> None:
        factory = DataProviderFactory()
        with patch.object(factory, "available_providers", return_value=[]):
            result = factory.bench_all()
            assert result == []

    def test_get_ranked_chain_for_ticker_uses_cache(self) -> None:
        factory = DataProviderFactory()
        factory._write_ranked_chain("sh", ["baostock", "akshare"])
        result = factory.get_ranked_chain_for_ticker("sh.600519")
        assert result == ["baostock", "akshare"]


# ═══════════════════════════════════════════════════════
# _get_provider_class for all names
# ═══════════════════════════════════════════════════════


class TestGetProviderClass:
    def test_all_provider_names(self) -> None:
        factory = DataProviderFactory()
        for name in [
            "baostock", "akshare", "mootdx", "tushare", "tonghuashun",
            "akshare_hk", "efinance", "easyquotation", "tencent", "adk",
        ]:
            cls = factory._get_provider_class(name)
            assert cls is not None, f"Provider {name} 应可加载"
            assert hasattr(cls, "name")


# ═══════════════════════════════════════════════════════
# get_data_factory with disabled providers
# ═══════════════════════════════════════════════════════


class TestGetDataFactory:
    def test_get_data_factory_disables_akshare(self, monkeypatch) -> None:
        """akshare_enabled=False 时 akshare 应从链中移除。"""
        reset_data_factory()
        try:
            mock_settings = __import__("types").SimpleNamespace(
                data_provider="baostock",
                data_fallback="",
                akshare_enabled=False,
                mootdx_enabled=True,
                tonghuashun_enabled=True,
            )
            with patch(
                "trade_krono_cli.data_providers.factory.get_settings",
                return_value=mock_settings,
            ):
                factory = get_data_factory()
                chain = factory.provider_chain
                assert "akshare" not in chain
        finally:
            reset_data_factory()

    def test_reset_data_factory_clears_singleton(self) -> None:
        reset_data_factory()
        f1 = get_data_factory()
        assert f1 is not None
        reset_data_factory()
        f2 = get_data_factory()
        assert f1 is not f2


# ════════════════════════════════════════════════════════
# _provider_chain_for_ticker — 向后兼容静态入口
# ════════════════════════════════════════════════════════


class TestProviderChainForTickerStatic:
    def test_provider_chain_for_ticker_delegates_to_singleton(self) -> None:
        """_provider_chain_for_ticker 应委托给全局单例的 _chain_for_ticker。"""
        reset_data_factory()
        try:
            factory = get_data_factory()
            with patch.object(
                factory, "_chain_for_ticker", return_value=["akshare"]
            ):
                result = DataProviderFactory._provider_chain_for_ticker("sh.600519")
                assert result == ["akshare"]
        finally:
            reset_data_factory()


# ════════════════════════════════════════════════════════
# _get_circuit — 熔断器懒初始化
# ════════════════════════════════════════════════════════


class TestGetCircuit:
    def test_get_circuit_creates_when_none(self) -> None:
        """_circuit 为 None 时，_get_circuit 应创建新实例。"""
        factory = DataProviderFactory()
        factory._circuit = None
        circuit = factory._get_circuit()
        assert circuit is not None
        assert factory._circuit is circuit

    def test_get_circuit_returns_existing(self) -> None:
        """_circuit 已设置时，_get_circuit 应直接返回。"""
        factory = DataProviderFactory()
        existing = factory._circuit
        assert factory._get_circuit() is existing


# ════════════════════════════════════════════════════════
# get_provider — 缓存命中与初始化异常
# ════════════════════════════════════════════════════════


class TestGetProviderCacheAndErrors:
    def test_get_provider_cache_hit(self) -> None:
        """第二次调用 get_provider 应命中缓存并直接返回。"""
        factory = DataProviderFactory()
        factory.get_provider("baostock")
        assert "baostock" in DataProviderFactory._instance_cache
        result = factory.get_provider("baostock")
        assert result is DataProviderFactory._instance_cache["baostock"]

    def test_get_provider_runtime_error(self) -> None:
        """Provider 初始化抛 RuntimeError 时应返回 None。"""
        factory = DataProviderFactory()

        class BadProvider:
            def __init__(self) -> None:
                raise RuntimeError("init failed")

        with patch.object(factory, "_get_provider_class", return_value=BadProvider):
            result = factory.get_provider("bad_provider")
            assert result is None

    def test_get_provider_import_error(self) -> None:
        """Provider 初始化抛 ImportError 时应返回 None。"""
        factory = DataProviderFactory()

        class BadProvider:
            def __init__(self) -> None:
                raise ImportError("dep missing")

        with patch.object(factory, "_get_provider_class", return_value=BadProvider):
            result = factory.get_provider("bad_provider")
            assert result is None


# ════════════════════════════════════════════════════════
# _fetch_kline_with_retry — 429 处理与不可达返回
# ════════════════════════════════════════════════════════


class TestFetchKlineWithRetryEdge:
    def _mock_kline(self) -> KlineData:
        return KlineData(
            timestamps=[datetime(2026, 8, 1)],
            open=[100.0],
            high=[101.0],
            low=[99.0],
            close=[100.5],
            volume=[1e6],
            amount=[1e8],
        )

    def test_fetch_kline_retry_429_in_retry_on(self) -> None:
        """retry_on 分支中 429 错误应记录并加倍延迟。"""
        factory = DataProviderFactory()
        provider = BaostockProvider()
        mock_kline = self._mock_kline()
        call_count = 0

        def flaky(*args: object, **kwargs: object) -> KlineData | None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("429 rate limited")
            return mock_kline

        with (
            patch.object(provider, "fetch_kline", side_effect=flaky),
            patch.object(factory, "_rate_limiter") as mock_rl,
        ):
            mock_rl.acquire.return_value = 0
            result = factory._fetch_kline_with_retry(
                provider,
                "baostock",
                "sh.600519",
                "2026-01-01",
                "2026-08-13",
                "d",
                "1",
                max_retries=2,
                base_delay=0.0,
                max_delay=1.0,
            )
            assert result is not None
            mock_rl.record_429.assert_called_with("baostock")

    def test_fetch_kline_429_in_except_exception(self) -> None:
        """except Exception 分支中 429 错误也应记录。"""
        factory = DataProviderFactory()
        provider = BaostockProvider()

        with (
            patch.object(
                provider, "fetch_kline", side_effect=Exception("429 rate limited")
            ),
            patch.object(factory, "_rate_limiter") as mock_rl,
        ):
            mock_rl.acquire.return_value = 0
            result = factory._fetch_kline_with_retry(
                provider,
                "baostock",
                "sh.600519",
                "2026-01-01",
                "2026-08-13",
                "d",
                "1",
                max_retries=1,
                base_delay=0.0,
                max_delay=1.0,
            )
            assert result is None
            mock_rl.record_429.assert_called_with("baostock")

    def test_fetch_kline_unreachable_return_none(self) -> None:
        """max_retries 为 -1 时循环不执行，直接返回 None。"""
        factory = DataProviderFactory()
        provider = BaostockProvider()
        mock_kline = self._mock_kline()

        with (
            patch.object(provider, "fetch_kline", return_value=mock_kline),
            patch.object(factory, "_rate_limiter") as mock_rl,
        ):
            mock_rl.acquire.return_value = 0
            result = factory._fetch_kline_with_retry(
                provider,
                "baostock",
                "sh.600519",
                "2026-01-01",
                "2026-08-13",
                "d",
                "1",
                max_retries=-1,
                base_delay=0.0,
                max_delay=1.0,
            )
            assert result is None


# ════════════════════════════════════════════════════════
# _retry_fetch — 429 处理与不可重试异常
# ════════════════════════════════════════════════════════


class TestRetryFetchEdge:
    def test_retry_fetch_429_in_retry_on(self) -> None:
        """retry_on 分支中 429 错误应记录并加倍延迟。"""
        factory = DataProviderFactory()
        call_count = 0

        def flaky() -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("429 rate limited")
            return "ok"

        with patch.object(factory, "_rate_limiter") as mock_rl:
            mock_rl.acquire.return_value = 0
            result = factory._retry_fetch(
                "test_provider",
                "sh.600519",
                flaky,
                "行情",
                max_retries=2,
                base_delay=0.0,
                max_delay=1.0,
            )
            assert result == "ok"
            mock_rl.record_429.assert_called_with("test_provider")

    def test_retry_fetch_429_in_except_exception(self) -> None:
        """except Exception 分支中 429 错误也应记录。"""
        factory = DataProviderFactory()

        def always_429() -> str:
            raise Exception("429 rate limited")

        with patch.object(factory, "_rate_limiter") as mock_rl:
            mock_rl.acquire.return_value = 0
            result = factory._retry_fetch(
                "test_provider",
                "sh.600519",
                always_429,
                "行情",
                max_retries=1,
                base_delay=0.0,
                max_delay=1.0,
            )
            assert result is None
            mock_rl.record_429.assert_called_with("test_provider")


# ════════════════════════════════════════════════════════
# fetch_kline — 跳过不支持 K 线 / 健康检查失败
# ════════════════════════════════════════════════════════


class TestFetchKlineEdge:
    def _mock_kline(self) -> KlineData:
        return KlineData(
            timestamps=[datetime(2026, 8, 1)],
            open=[100.0],
            high=[101.0],
            low=[99.0],
            close=[100.5],
            volume=[1e6],
            amount=[1e8],
        )

    def test_fetch_kline_skips_unsupported_kline(self) -> None:
        """不支持 K 线的 Provider 应被跳过。"""
        factory = DataProviderFactory(primary="p1", fallbacks=["p2"])
        p1 = MagicMock()
        p1.supports_kline = False
        p2 = MagicMock()
        p2.supports_kline = True
        p2.fetch_kline.return_value = self._mock_kline()

        with (
            patch.object(factory, "get_provider", side_effect=[p1, p2]),
            patch.object(p1, "health_check", return_value=True),
            patch.object(p2, "health_check", return_value=True),
            patch.object(factory, "_rate_limiter") as mock_rl,
        ):
            mock_rl.acquire.return_value = 0
            result = factory.fetch_kline("sh.600519", "2026-01-01", "2026-08-13")
            assert result is not None

    def test_fetch_kline_health_check_fail(self) -> None:
        """健康检查失败的 Provider 应被跳过。"""
        factory = DataProviderFactory(primary="p1", fallbacks=["p2"])
        p1 = MagicMock()
        p1.supports_kline = True
        p2 = MagicMock()
        p2.supports_kline = True
        p2.fetch_kline.return_value = self._mock_kline()

        with (
            patch.object(factory, "get_provider", side_effect=[p1, p2]),
            patch.object(p1, "health_check", return_value=False),
            patch.object(p2, "health_check", return_value=True),
            patch.object(factory, "_rate_limiter") as mock_rl,
        ):
            mock_rl.acquire.return_value = 0
            result = factory.fetch_kline("sh.600519", "2026-01-01", "2026-08-13")
            assert result is not None


# ════════════════════════════════════════════════════════
# fetch_kline_from_provider — 限流等待
# ════════════════════════════════════════════════════════


class TestFetchKlineFromProviderEdge:
    def test_fetch_kline_from_provider_rate_limit_sleep(self) -> None:
        """限流等待时应 sleep。"""
        factory = DataProviderFactory()
        provider = BaostockProvider()
        mock_kline = KlineData(
            timestamps=[datetime(2026, 8, 1)],
            open=[100.0],
            high=[101.0],
            low=[99.0],
            close=[100.5],
            volume=[1e6],
            amount=[1e8],
        )

        with (
            patch.object(factory, "get_provider", return_value=provider),
            patch.object(provider, "fetch_kline", return_value=mock_kline),
            patch.object(provider, "health_check", return_value=True),
            patch.object(factory, "_rate_limiter") as mock_rl,
            patch("time.sleep") as mock_sleep,
        ):
            mock_rl.acquire.return_value = 0.5
            result = factory.fetch_kline_from_provider(
                "baostock", "sh.600519", "2026-01-01", "2026-08-13",
            )
            assert result is not None
            mock_sleep.assert_any_call(0.5)


# ════════════════════════════════════════════════════════
# fetch_quote — 健康检查失败 / 限流等待 / 空结果
# ════════════════════════════════════════════════════════


class TestFetchQuoteEdge:
    def test_fetch_quote_health_check_fail(self) -> None:
        """健康检查失败时应跳过该 Provider。"""
        factory = DataProviderFactory(primary="p1", fallbacks=["p2"])
        p1 = MagicMock()
        p1.supports_quote = True
        p2 = MagicMock()
        p2.supports_quote = True
        mock_quote = RealtimeQuote(ticker="sh.600519", price=100.0, source="p2")
        p2.fetch_quote.return_value = mock_quote

        with (
            patch.object(factory, "get_provider", side_effect=[p1, p2]),
            patch.object(p1, "health_check", return_value=False),
            patch.object(p2, "health_check", return_value=True),
            patch.object(factory, "_rate_limiter") as mock_rl,
        ):
            mock_rl.acquire.return_value = 0
            result = factory.fetch_quote("sh.600519")
            assert result is not None
            assert result.price == 100.0

    def test_fetch_quote_rate_limit_sleep(self) -> None:
        """限流等待时应 sleep。"""
        factory = DataProviderFactory()
        provider = MagicMock()
        provider.supports_quote = True
        mock_quote = RealtimeQuote(ticker="sh.600519", price=100.0, source="test")
        provider.fetch_quote.return_value = mock_quote

        with (
            patch.object(factory, "get_provider", return_value=provider),
            patch.object(provider, "health_check", return_value=True),
            patch.object(factory, "_rate_limiter") as mock_rl,
            patch("time.sleep") as mock_sleep,
        ):
            mock_rl.acquire.return_value = 0.5
            result = factory.fetch_quote("sh.600519")
            assert result is not None
            mock_sleep.assert_any_call(0.5)

    def test_fetch_quote_empty_returns_none(self) -> None:
        """Provider 返回空行情时应直接返回 None，不降级。"""
        factory = DataProviderFactory()
        p1 = MagicMock()
        p1.supports_quote = True
        p1.fetch_quote.return_value = None  # 空结果

        with (
            patch.object(factory, "get_provider", return_value=p1),
            patch.object(p1, "health_check", return_value=True),
            patch.object(factory, "_rate_limiter") as mock_rl,
        ):
            mock_rl.acquire.return_value = 0
            result = factory.fetch_quote("sh.600519")
            assert result is None


# ════════════════════════════════════════════════════════
# fetch_metadata — 跳过不支持 / 健康检查 / 限流 / 空结果 / 字段合并
# ════════════════════════════════════════════════════════


class TestFetchMetadataEdge:
    def test_fetch_metadata_skips_unsupported(self) -> None:
        """不支持元数据的 Provider 应被跳过。"""
        factory = DataProviderFactory(primary="p1", fallbacks=["p2"])
        p1 = MagicMock()
        p1.supports_metadata = False
        mock_meta = StockMetadata(ticker="sh.600519", industry="白酒", source="p2")
        p2 = MagicMock()
        p2.supports_metadata = True
        p2.fetch_metadata.return_value = mock_meta

        with (
            patch.object(factory, "get_provider", side_effect=[p1, p2]),
            patch.object(p1, "health_check", return_value=True),
            patch.object(p2, "health_check", return_value=True),
            patch.object(factory, "_rate_limiter") as mock_rl,
        ):
            mock_rl.acquire.return_value = 0
            result = factory.fetch_metadata("sh.600519")
            assert result is not None
            assert result.industry == "白酒"

    def test_fetch_metadata_health_check_fail(self) -> None:
        """健康检查失败时应跳过。"""
        factory = DataProviderFactory(primary="p1", fallbacks=["p2"])
        p1 = MagicMock()
        p1.supports_metadata = True
        mock_meta = StockMetadata(ticker="sh.600519", industry="白酒", source="p2")
        p2 = MagicMock()
        p2.supports_metadata = True
        p2.fetch_metadata.return_value = mock_meta

        with (
            patch.object(factory, "get_provider", side_effect=[p1, p2]),
            patch.object(p1, "health_check", return_value=False),
            patch.object(p2, "health_check", return_value=True),
            patch.object(factory, "_rate_limiter") as mock_rl,
        ):
            mock_rl.acquire.return_value = 0
            result = factory.fetch_metadata("sh.600519")
            assert result is not None

    def test_fetch_metadata_rate_limit_sleep(self) -> None:
        """限流等待时应 sleep。"""
        factory = DataProviderFactory()
        provider = MagicMock()
        provider.supports_metadata = True
        mock_meta = StockMetadata(ticker="sh.600519", industry="白酒", source="test")
        provider.fetch_metadata.return_value = mock_meta

        with (
            patch.object(factory, "get_provider", return_value=provider),
            patch.object(provider, "health_check", return_value=True),
            patch.object(factory, "_rate_limiter") as mock_rl,
            patch("time.sleep") as mock_sleep,
        ):
            mock_rl.acquire.return_value = 0.5
            result = factory.fetch_metadata("sh.600519")
            assert result is not None
            mock_sleep.assert_any_call(0.5)

    def test_fetch_metadata_meta_none_continues(self) -> None:
        """Provider 返回 None 时应继续下一个 Provider。"""
        factory = DataProviderFactory(primary="p1", fallbacks=["p2"])
        p1 = MagicMock()
        p1.supports_metadata = True
        p1.fetch_metadata.return_value = None  # 空结果
        mock_meta = StockMetadata(ticker="sh.600519", industry="白酒", source="p2")
        p2 = MagicMock()
        p2.supports_metadata = True
        p2.fetch_metadata.return_value = mock_meta

        with (
            patch.object(factory, "get_provider", side_effect=[p1, p2]),
            patch.object(p1, "health_check", return_value=True),
            patch.object(p2, "health_check", return_value=True),
            patch.object(factory, "_rate_limiter") as mock_rl,
        ):
            mock_rl.acquire.return_value = 0
            result = factory.fetch_metadata("sh.600519")
            assert result is not None
            assert result.industry == "白酒"

    def test_fetch_metadata_merges_is_st_and_source(self) -> None:
        """is_st 和 source 字段应正确合并。"""
        factory = DataProviderFactory(primary="p1")
        provider = MagicMock()
        provider.supports_metadata = True
        meta = StockMetadata(
            ticker="sh.600519", is_st=True, source="p1", pe_ttm=15.0
        )
        provider.fetch_metadata.return_value = meta

        with (
            patch.object(factory, "get_provider", return_value=provider),
            patch.object(provider, "health_check", return_value=True),
            patch.object(factory, "_rate_limiter") as mock_rl,
        ):
            mock_rl.acquire.return_value = 0
            result = factory.fetch_metadata("sh.600519")
            assert result is not None
            assert result.is_st is True
            assert result.source == "p1"


# ════════════════════════════════════════════════════════
# fetch_quotes_batch — remaining 为空 / 限流 / 异常
# ════════════════════════════════════════════════════════


class TestFetchQuotesBatchEdge:
    def test_fetch_quotes_batch_remaining_empty_break(self) -> None:
        """remaining 为空时应 break，不再调用后续 Provider。"""
        factory = DataProviderFactory(primary="p1", fallbacks=["p2"])
        q1 = RealtimeQuote(ticker="sh.600519", price=100.0, source="p1")
        q2 = RealtimeQuote(ticker="sz.000858", price=50.0, source="p1")
        p1 = MagicMock()
        p1.supports_quote = True
        p1.fetch_quotes_batch.return_value = {"sh.600519": q1, "sz.000858": q2}
        p2 = MagicMock()
        p2.supports_quote = True

        with (
            patch.object(factory, "get_provider", side_effect=[p1, p2]),
            patch.object(p1, "health_check", return_value=True),
            patch.object(p2, "health_check", return_value=True),
            patch.object(factory, "_rate_limiter") as mock_rl,
        ):
            mock_rl.acquire.return_value = 0
            result = factory.fetch_quotes_batch(["sh.600519", "sz.000858"])
            assert len(result) == 2
            p2.fetch_quotes_batch.assert_not_called()  # 因 remaining 已空而 break

    def test_fetch_quotes_batch_rate_limit_sleep(self) -> None:
        """限流等待时应 sleep。"""
        factory = DataProviderFactory()
        q = RealtimeQuote(ticker="sh.600519", price=100.0, source="p1")
        p = MagicMock()
        p.supports_quote = True
        p.fetch_quotes_batch.return_value = {"sh.600519": q}

        with (
            patch.object(factory, "get_provider", return_value=p),
            patch.object(p, "health_check", return_value=True),
            patch.object(factory, "_rate_limiter") as mock_rl,
            patch("time.sleep") as mock_sleep,
        ):
            mock_rl.acquire.return_value = 0.5
            result = factory.fetch_quotes_batch(["sh.600519"])
            assert "sh.600519" in result
            mock_sleep.assert_any_call(0.5)

    def test_fetch_quotes_batch_exception(self) -> None:
        """Provider 批量方法抛异常时应记录失败并继续。"""
        factory = DataProviderFactory(primary="p1", fallbacks=["p2"])
        p1 = MagicMock()
        p1.supports_quote = True
        p1.fetch_quotes_batch.side_effect = Exception("batch failed")
        p2 = MagicMock()
        p2.supports_quote = True
        q = RealtimeQuote(ticker="sh.600519", price=100.0, source="p2")
        p2.fetch_quotes_batch.return_value = {"sh.600519": q}

        with (
            patch.object(factory, "get_provider", side_effect=[p1, p2]),
            patch.object(p1, "health_check", return_value=True),
            patch.object(p2, "health_check", return_value=True),
            patch.object(factory, "_rate_limiter") as mock_rl,
        ):
            mock_rl.acquire.return_value = 0
            result = factory.fetch_quotes_batch(["sh.600519"])
            assert "sh.600519" in result  # p2 成功


# ════════════════════════════════════════════════════════
# fetch_metadata_batch — 完整流程
# ════════════════════════════════════════════════════════


class TestFetchMetadataBatchFull:
    def test_fetch_metadata_batch_full_flow(self) -> None:
        """批量元数据获取的完整流程。"""
        factory = DataProviderFactory(primary="p1", fallbacks=["p2"])
        meta1 = StockMetadata(ticker="sh.600519", industry="白酒", source="p1")
        meta2 = StockMetadata(ticker="sz.000858", industry="银行", source="p2")
        p1 = MagicMock()
        p1.supports_metadata = True
        p1.fetch_metadata_batch.return_value = {"sh.600519": meta1}
        p2 = MagicMock()
        p2.supports_metadata = True
        p2.fetch_metadata_batch.return_value = {"sz.000858": meta2}

        with (
            patch.object(factory, "get_provider", side_effect=[p1, p2]),
            patch.object(p1, "health_check", return_value=True),
            patch.object(p2, "health_check", return_value=True),
            patch.object(factory, "_rate_limiter") as mock_rl,
        ):
            mock_rl.acquire.return_value = 0
            result = factory.fetch_metadata_batch(["sh.600519", "sz.000858"])
            assert "sh.600519" in result
            assert "sz.000858" in result
            assert result["sh.600519"].industry == "白酒"
            assert result["sz.000858"].industry == "银行"


# ════════════════════════════════════════════════════════
# health_check_all — provider 为 None / 异常
# ════════════════════════════════════════════════════════


class TestHealthCheckAllEdge:
    def test_health_check_all_provider_none(self) -> None:
        """get_provider 返回 None 时应标记为 False。"""
        factory = DataProviderFactory()
        with patch.object(factory, "get_provider", return_value=None):
            result = factory.health_check_all()
            for name in factory.provider_chain:
                assert result[name] is False

    def test_health_check_all_exception(self) -> None:
        """health_check 抛异常时应标记为 False。"""
        factory = DataProviderFactory()
        provider = MagicMock()
        provider.health_check.side_effect = RuntimeError("check failed")

        with patch.object(factory, "get_provider", return_value=provider):
            result = factory.health_check_all()
            for name in factory.provider_chain:
                assert result[name] is False


# ════════════════════════════════════════════════════════
# _benchmark_provider — 委托 factory_benchmark
# ════════════════════════════════════════════════════════


class TestBenchmarkProvider:
    def test_benchmark_provider(self) -> None:
        """_benchmark_provider 应委托给 _bench_factory_provider。"""
        factory = DataProviderFactory()
        with patch(
            "trade_krono_cli.data_providers.factory_benchmark._bench_factory_provider"
        ) as mock_bench:
            mock_bench.return_value = MagicMock()
            result = factory._benchmark_provider("baostock", "sh.600519")
            assert result is not None
            mock_bench.assert_called_once_with(factory, "baostock", "sh.600519")


# ════════════════════════════════════════════════════════
# _get_provider_class — 导入失败
# ════════════════════════════════════════════════════════


class TestGetProviderClassImport:
    def test_get_provider_class_import_error(self) -> None:
        """_get_provider_class 中导入失败时应返回 None。"""
        factory = DataProviderFactory()
        factory._PROVIDER_REGISTRY.pop("baostock", None)
        with patch("builtins.__import__", side_effect=ImportError("mock import error")):
            cls = factory._get_provider_class("baostock")
            assert cls is None


# ════════════════════════════════════════════════════════
# get_data_factory — 主数据源被禁用
# ════════════════════════════════════════════════════════


class TestGetDataFactoryDisabledPrimary:
    def test_get_data_factory_primary_disabled(self) -> None:
        """主数据源被禁用时应回退到备用源并记录警告。"""
        reset_data_factory()
        try:
            mock_settings = __import__("types").SimpleNamespace(
                data_provider="akshare",
                data_fallback="",
                akshare_enabled=False,
                mootdx_enabled=True,
                tonghuashun_enabled=True,
            )
            with patch(
                "trade_krono_cli.data_providers.factory.get_settings",
                return_value=mock_settings,
            ):
                factory = get_data_factory()
                chain = factory.provider_chain
                assert "akshare" not in chain
                assert factory.primary != "akshare"
        finally:
            reset_data_factory()
