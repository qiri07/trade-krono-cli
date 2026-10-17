"""dataProvider factory 补充边界测试。

补充 test_data_provider_factory.py 未覆盖的分支：
- 港股（hk.）ticker 的 akshare_hk 置顶链
- _provider_chain_for_ticker 无点号 ticker
- fetch_metadata 全部 provider 无结果 → None
- fetch_kline/fetch_quote 中 provider 不支持该维度 → 跳过
- reset_data_factory 清空全部缓存
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from trade_krono_cli.data_providers.base import KlineData
from trade_krono_cli.data_providers.factory import (
    DataProviderFactory,
    reset_data_factory,
)


class TestHKProviderChain:
    def test_hk_ticker_forces_akshare_hk_first(self) -> None:
        factory = DataProviderFactory(
            primary="akshare",
            fallbacks=["mootdx", "tushare", "akshare_hk"],
        )
        with patch.object(factory, "_get_cached_ranked_chain", return_value=None):
            chain = factory._provider_chain_for_ticker("hk.00700")
        assert chain[0] == "akshare_hk"

    def test_bj_ticker_forces_tonghuashun_first(self) -> None:
        factory = DataProviderFactory(
            primary="baostock",
            fallbacks=["akshare", "tonghuashun"],
        )
        with patch.object(factory, "_get_cached_ranked_chain", return_value=None):
            chain = factory._provider_chain_for_ticker("bj.830001")
        assert chain[0] == "tonghuashun"

    def test_ticker_without_dot_uses_self_as_type(self) -> None:
        factory = DataProviderFactory(primary="baostock", fallbacks=[])
        called_with: list[str] = []

        def _fake_cache(ticker_type: str):
            called_with.append(ticker_type)
            return None

        # 直接调用类静态方法需绕过 get_data_factory() 单例：
        # _provider_chain_for_ticker 是 @staticmethod，内部调用 get_data_factory()。
        # 用实例 + patch 单例更可靠，这里改为 patch get_data_factory。
        with (
            patch("trade_krono_cli.data_providers.factory.get_data_factory", return_value=factory),
            patch.object(factory, "_get_cached_ranked_chain", side_effect=_fake_cache),
        ):
            DataProviderFactory._provider_chain_for_ticker("IC2610")
        assert "IC2610" in called_with


class TestFetchMetadataEmpty:
    def test_fetch_metadata_all_none_returns_none(self) -> None:
        factory = DataProviderFactory(primary="baostock", fallbacks=[])

        mock_provider = MagicMock()
        mock_provider.supports_metadata = True
        mock_provider.health_check.return_value = True
        mock_provider.fetch_metadata.return_value = None

        with (
            patch.object(factory, "get_provider", return_value=mock_provider),
            patch.object(factory, "_provider_chain_for_ticker", return_value=["baostock"]),
        ):
            result = factory.fetch_metadata("sh.600519")
        assert result is None


class TestFetchDimensionSkip:
    def test_fetch_kline_skips_provider_without_kline_support(self) -> None:
        factory = DataProviderFactory(primary="baostock", fallbacks=[])

        mock_provider = MagicMock()
        mock_provider.supports_kline = False
        mock_provider.health_check.return_value = True
        mock_provider.fetch_kline = MagicMock()

        with (
            patch.object(factory, "get_provider", return_value=mock_provider),
            patch.object(factory, "_provider_chain_for_ticker", return_value=["baostock"]),
        ):
            result = factory.fetch_kline("sh.600519", "2026-01-01", "2026-01-31")
        assert result is None
        mock_provider.fetch_kline.assert_not_called()

    def test_fetch_kline_skips_unhealthy_provider(self) -> None:
        factory = DataProviderFactory(primary="baostock", fallbacks=[])

        mock_provider = MagicMock()
        mock_provider.supports_kline = True
        mock_provider.health_check.return_value = False

        with (
            patch.object(factory, "get_provider", return_value=mock_provider),
            patch.object(factory, "_provider_chain_for_ticker", return_value=["baostock"]),
        ):
            result = factory.fetch_kline("sh.600519", "2026-01-01", "2026-01-31")
        assert result is None

    def test_fetch_quote_skips_unsupported_provider(self) -> None:
        factory = DataProviderFactory(primary="baostock", fallbacks=[])

        mock_provider = MagicMock()
        mock_provider.supports_quote = False
        mock_provider.health_check.return_value = True
        mock_provider.fetch_quote = MagicMock()

        with (
            patch.object(factory, "get_provider", return_value=mock_provider),
            patch.object(factory, "_provider_chain_for_ticker", return_value=["baostock"]),
        ):
            result = factory.fetch_quote("sh.600519")
        assert result is None
        mock_provider.fetch_quote.assert_not_called()

    def test_fetch_quote_provider_exception_falls_to_none(self) -> None:
        factory = DataProviderFactory(primary="baostock", fallbacks=[])

        mock_provider = MagicMock()
        mock_provider.supports_quote = True
        mock_provider.health_check.return_value = True
        mock_provider.fetch_quote.side_effect = Exception("boom")

        with (
            patch.object(factory, "get_provider", return_value=mock_provider),
            patch.object(factory, "_provider_chain_for_ticker", return_value=["baostock"]),
        ):
            assert factory.fetch_quote("sh.600519") is None

    def test_fetch_kline_empty_result_continues_to_next(self) -> None:
        """第一个 provider 返回空结果，第二个命中。"""
        factory = DataProviderFactory(primary="baostock", fallbacks=["akshare"])

        empty_kline = KlineData(
            timestamps=[],
            open=[],
            high=[],
            low=[],
            close=[],
            volume=[],
            amount=[],
        )
        good_kline = KlineData(
            timestamps=[__import__("datetime").datetime(2026, 1, 5)],
            open=[100.0],
            high=[101.0],
            low=[99.0],
            close=[100.5],
            volume=[1000.0],
            amount=[1e8],
        )

        p1 = MagicMock()
        p1.supports_kline = True
        p1.health_check.return_value = True
        p1.fetch_kline.return_value = empty_kline

        p2 = MagicMock()
        p2.supports_kline = True
        p2.health_check.return_value = True
        p2.fetch_kline.return_value = good_kline

        with (
            patch.object(
                factory,
                "get_provider",
                side_effect=lambda name: {"baostock": p1, "akshare": p2}[name],
            ),
            patch.object(
                factory,
                "_provider_chain_for_ticker",
                return_value=["baostock", "akshare"],
            ),
        ):
            result = factory.fetch_kline("sh.600519", "2026-01-01", "2026-01-31")
        assert result is not None
        assert result.length == 1
        p2.fetch_kline.assert_called_once()


class TestResetDataFactory:
    def test_reset_clears_instance_and_rank_caches(self) -> None:
        factory = DataProviderFactory(primary="baostock")
        # 填充缓存
        mock_instance = MagicMock()
        factory._instance_cache["baostock"] = mock_instance
        factory._rank_cache["sh"] = (["baostock", "akshare"], 0.0)

        reset_data_factory()

        assert factory._instance_cache == {}
        assert factory._rank_cache == {}


class TestProviderChain:
    def test_provider_chain_dedupes_primary_in_fallbacks(self) -> None:
        factory = DataProviderFactory(primary="akshare", fallbacks=["akshare", "mootdx"])
        assert factory.provider_chain == ["akshare", "mootdx"]

    def test_custom_fallbacks_default_when_none(self) -> None:
        factory = DataProviderFactory(primary="baostock")
        assert factory.fallbacks == DataProviderFactory._default_fallbacks()
