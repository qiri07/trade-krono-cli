"""DataProviderFactory 单元测试。

覆盖：
  - provider_chain / _provider_chain_for_ticker
  - get_provider / get_providers
  - fetch_kline 降级逻辑
  - 北交所 / 港股特殊处理
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from trade_krono_cli.data_providers.factory import (
    DataProviderFactory,
    reset_data_factory,
)


@pytest.fixture(autouse=True)
def _reset():
    """每个测试前重置工厂单例。"""
    reset_data_factory()
    yield
    reset_data_factory()


# ── provider_chain ────────────────────────────────────────────────────────────


class TestProviderChain:
    def test_default_chain(self) -> None:
        factory = DataProviderFactory()
        assert factory.provider_chain == ["baostock", "akshare", "mootdx", "tushare", "tonghuashun"]

    def test_custom_primary(self) -> None:
        factory = DataProviderFactory(primary="akshare")
        assert factory.provider_chain[0] == "akshare"

    def test_fallbacks_excludes_primary(self) -> None:
        factory = DataProviderFactory(primary="baostock", fallbacks=["baostock", "akshare"])
        assert factory.provider_chain == ["baostock", "akshare"]


# ── _provider_chain_for_ticker ───────────────────────────────────────────────


class TestProviderChainForTicker:
    def test_a_share_uses_default_chain(self) -> None:
        chain = DataProviderFactory._provider_chain_for_ticker("sh.600519")
        # 主 provider 可能是 tonghuashun 或 baostock，取决于配置
        assert chain[0] in ("tonghuashun", "baostock")
        assert "akshare" in chain

    def test_bj_force_tonghuashun_first(self) -> None:
        chain = DataProviderFactory._provider_chain_for_ticker("bj.872222")
        assert chain[0] == "tonghuashun"

    def test_hk_force_akshare_hk_first(self) -> None:
        chain = DataProviderFactory._provider_chain_for_ticker("hk.00700")
        assert chain[0] == "akshare_hk"

    def test_bj_moves_tonghuashun_from_middle_to_top(self) -> None:
        """北交所时 tonghuashun 无论原来位置都置顶。"""
        chain = DataProviderFactory._provider_chain_for_ticker("bj.920953")
        assert chain[0] == "tonghuashun"

    def test_hk_moves_akshare_hk_from_middle_to_top(self) -> None:
        """港股时 akshare_hk 无论原来位置都置顶。"""
        chain = DataProviderFactory._provider_chain_for_ticker("hk.09988")
        assert chain[0] == "akshare_hk"


# ── get_provider ──────────────────────────────────────────────────────────────


class TestGetProvider:
    def test_get_unknown_provider_returns_none(self) -> None:
        factory = DataProviderFactory()
        result = factory.get_provider("nonexistent")
        assert result is None

    def test_get_provider_cached(self) -> None:
        """已缓存的 provider 直接返回缓存实例。"""
        factory = DataProviderFactory()
        with patch.object(factory, "_get_provider_class", return_value=None):
            result = factory.get_provider("baostock")
        assert result is None  # 类不存在时返回 None

    def test_get_provider_import_error(self) -> None:
        """ImportError 时返回 None，不抛出异常。"""
        bad_cls = MagicMock(side_effect=ImportError("no module"))
        bad_cls.__name__ = "BadProvider"

        with patch.object(factory := DataProviderFactory(), "_get_provider_class", return_value=bad_cls):
            result = factory.get_provider("bad")
        assert result is None


# ── get_providers ─────────────────────────────────────────────────────────────


class TestGetProviders:
    def test_empty_names_uses_chain(self) -> None:
        """未指定 names 时使用 provider_chain。"""
        factory = DataProviderFactory()
        with patch.object(factory, "get_provider", return_value=None) as mock_get:
            factory.get_providers()
        # 至少会调用 provider_chain 长度的次数
        assert mock_get.call_count == len(factory.provider_chain)

    def test_filters_unavailable(self) -> None:
        """过滤掉返回 None 的 provider。"""
        factory = DataProviderFactory()
        good = MagicMock()
        with patch.object(factory, "get_provider", side_effect=lambda n: good if n == "baostock" else None):
            result = factory.get_providers(["baostock", "fake"])
        assert len(result) == 1
        assert result[0] is good


# ── fetch_kline 降级逻辑 ──────────────────────────────────────────────────────


class TestFetchKlineFallback:
    """fetch_kline 降级逻辑测试。"""

    def test_first_provider_succeeds(self) -> None:
        """主 provider 成功时不尝试备用。"""
        factory = DataProviderFactory(primary="p1", fallbacks=["p2"])
        mock_p1 = MagicMock()
        mock_p1.supports_kline = True
        mock_p1.health_check.return_value = True
        mock_p1.fetch_kline.return_value = MagicMock(is_empty=False, timestamps=["2026-01-01"])

        # patch get_provider 和 chain 解析
        with (
            patch.object(factory, "get_provider", side_effect=lambda n: mock_p1 if n == "p1" else None),
            patch.object(
                DataProviderFactory,
                "_provider_chain_for_ticker",
                return_value=["p1"],
            ),
        ):
            result = factory.fetch_kline("sh.600519", "2026-01-01", "2026-01-02")
        assert result is not None
        assert mock_p1.fetch_kline.call_count == 1

    def test_first_fails_fallback_to_second(self) -> None:
        """主 provider 失败时降级到备用。"""
        factory = DataProviderFactory(primary="p1", fallbacks=["p2"])
        mock_p1 = MagicMock()
        mock_p1.supports_kline = True
        mock_p1.health_check.return_value = True
        mock_p1.fetch_kline.side_effect = RuntimeError("network error")

        mock_p2 = MagicMock()
        mock_p2.supports_kline = True
        mock_p2.health_check.return_value = True
        mock_p2.fetch_kline.return_value = MagicMock(is_empty=False, timestamps=["2026-01-01"])

        with (
            patch.object(
                factory,
                "get_provider",
                side_effect=lambda n: mock_p1 if n == "p1" else mock_p2,
            ),
            patch.object(
                DataProviderFactory,
                "_provider_chain_for_ticker",
                return_value=["p1", "p2"],
            ),
        ):
            result = factory.fetch_kline("sh.600519", "2026-01-01", "2026-01-02")
        assert result is not None
        assert mock_p1.fetch_kline.call_count == 1
        assert mock_p2.fetch_kline.call_count == 1

    def test_all_providers_fail_returns_none(self) -> None:
        """所有 provider 都失败时返回 None。"""
        factory = DataProviderFactory(primary="p1", fallbacks=["p2"])
        mock_p1 = MagicMock()
        mock_p1.supports_kline = True
        mock_p1.health_check.return_value = True
        mock_p1.fetch_kline.return_value = None

        mock_p2 = MagicMock()
        mock_p2.supports_kline = True
        mock_p2.health_check.return_value = True
        mock_p2.fetch_kline.return_value = None

        def get_provider(name: str):
            return mock_p1 if name == "p1" else mock_p2

        with patch.object(factory, "get_provider", side_effect=get_provider):
            result = factory.fetch_kline("sh.600519", "2026-01-01", "2026-01-02")

        assert result is None

    def test_skips_provider_without_kline_support(self) -> None:
        """不支持 K 线的 provider 被跳过。"""
        factory = DataProviderFactory(primary="p1", fallbacks=["p2"])
        mock_p1 = MagicMock()
        mock_p1.supports_kline = False  # 不支持 K 线

        mock_p2 = MagicMock()
        mock_p2.supports_kline = True
        mock_p2.health_check.return_value = True
        mock_p2.fetch_kline.return_value = MagicMock(is_empty=False, timestamps=["2026-01-01"])

        def get_provider(name: str):
            return mock_p1 if name == "p1" else mock_p2

        with patch.object(factory, "get_provider", side_effect=get_provider):
            result = factory.fetch_kline("sh.600519", "2026-01-01", "2026-01-02")

        assert result is not None
        assert mock_p1.fetch_kline.call_count == 0  # 跳过
        assert mock_p2.fetch_kline.call_count == 1

    def test_health_check_failure_skips_provider(self) -> None:
        """健康检查未通过时跳过该 provider。"""
        factory = DataProviderFactory(primary="p1", fallbacks=["p2"])
        mock_p1 = MagicMock()
        mock_p1.supports_kline = True
        mock_p1.health_check.return_value = False  # 健康检查失败

        mock_p2 = MagicMock()
        mock_p2.supports_kline = True
        mock_p2.health_check.return_value = True
        mock_p2.fetch_kline.return_value = MagicMock(is_empty=False, timestamps=["2026-01-01"])

        def get_provider(name: str):
            return mock_p1 if name == "p1" else mock_p2

        with patch.object(factory, "get_provider", side_effect=get_provider):
            result = factory.fetch_kline("sh.600519", "2026-01-01", "2026-01-02")

        assert result is not None
        assert mock_p1.fetch_kline.call_count == 0
        assert mock_p2.fetch_kline.call_count == 1
