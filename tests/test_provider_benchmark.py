"""测试 Provider Benchmark 自适应优先级机制。"""

from __future__ import annotations

import time
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from trade_krono_cli.data_providers.base import KlineData
from trade_krono_cli.data_providers.factory import (
    DataProviderFactory,
    _BenchResult,
    reset_data_factory,
)


@pytest.fixture(autouse=True)
def _clear_benchmark_cache():
    """每个测试前后清理 benchmark 缓存。"""
    reset_data_factory()
    DataProviderFactory._rank_cache.clear()
    yield
    DataProviderFactory._rank_cache.clear()
    reset_data_factory()


def _make_mock_provider(success: bool = True, latency_ms: float = 50.0) -> MagicMock:
    """创建一个返回 KlineData 的 mock provider。"""
    mock = MagicMock()
    mock.supports_kline = True
    mock.supports_quote = False
    mock.supports_metadata = False
    mock.health_check.return_value = success
    if success:
        mock.fetch_kline.return_value = KlineData(
            timestamps=[datetime(2026, 9, 1)],
            open=[100.0],
            high=[101.0],
            low=[99.0],
            close=[100.5],
            volume=[1e6],
            amount=[1e8],
        )
        # 模拟延迟
        original_fetch = mock.fetch_kline

        def delayed_fetch(*args, **kwargs):
            time.sleep(latency_ms / 1000)
            return original_fetch(*args, **kwargs)

        mock.fetch_kline.side_effect = delayed_fetch
    else:
        mock.fetch_kline.side_effect = Exception("Connection failed")
    return mock


class TestBenchResult:
    """_BenchResult dataclass 测试。"""

    def test_create_result(self) -> None:
        result = _BenchResult(name="baostock", latency_ms=42.0, success=True)
        assert result.name == "baostock"
        assert result.latency_ms == 42.0
        assert result.success is True

    def test_frozen_cannot_modify(self) -> None:
        result = _BenchResult(name="test", latency_ms=1.0, success=True)
        with pytest.raises(AttributeError):
            result.name = "other"


class TestGetCachedRankedChain:
    """_get_cached_ranked_chain 缓存读取测试。"""

    def test_cache_miss_returns_none(self) -> None:
        factory = DataProviderFactory()
        assert factory._get_cached_ranked_chain("sh") is None

    def test_cache_hit_returns_chain(self) -> None:
        factory = DataProviderFactory()
        factory._write_ranked_chain("sh", ["baostock", "akshare"])
        result = factory._get_cached_ranked_chain("sh")
        assert result is not None
        assert result[0] == ["baostock", "akshare"]

    def test_expired_cache_returns_none(self) -> None:
        factory = DataProviderFactory()
        # 手动写入过期缓存
        with factory._rank_lock:
            factory._rank_cache["sz"] = (["akshare"], time.time() - 700)
        assert factory._get_cached_ranked_chain("sz") is None


class TestBenchAll:
    """bench_all 并发 benchmark 测试。"""

    def test_empty_providers_returns_empty(self) -> None:
        """没有可用 provider 时返回空列表。"""
        factory = DataProviderFactory(primary="akshare", fallbacks=[])
        # 没有任何 provider 可初始化
        with patch.object(factory, "available_providers", return_value=[]):
            results = factory.bench_all()
        assert results == []

    def test_sorted_by_latency(self) -> None:
        """结果按延迟升序排列。"""
        factory = DataProviderFactory()
        # 完全 mock available_providers 和 _benchmark_provider
        with patch.object(factory, "available_providers", return_value=["p1", "p2", "p3"]):
            with patch.object(
                factory,
                "_benchmark_provider",
                side_effect=[
                    _BenchResult(name="p2", latency_ms=100.0, success=True),
                    _BenchResult(name="p1", latency_ms=50.0, success=True),
                    _BenchResult(name="p3", latency_ms=200.0, success=True),
                ],
            ):
                results = factory.bench_all()
        assert len(results) == 3
        assert [r.name for r in results] == ["p1", "p2", "p3"]

    def test_failed_providers_sorted_last(self) -> None:
        """失败的 provider 排在末尾。"""
        factory = DataProviderFactory()
        with patch.object(factory, "available_providers", return_value=["fast", "broken", "slow"]):
            with patch.object(
                factory,
                "_benchmark_provider",
                side_effect=[
                    _BenchResult(name="slow", latency_ms=300.0, success=True),
                    _BenchResult(name="broken", latency_ms=float("inf"), success=False),
                    _BenchResult(name="fast", latency_ms=50.0, success=True),
                ],
            ):
                results = factory.bench_all()
        names = [r.name for r in results]
        # broken 排最后，其他按延迟排序
        assert names.index("broken") > names.index("fast")
        assert names.index("broken") > names.index("slow")


class TestGetRankedChainForTicker:
    """get_ranked_chain_for_ticker 缓存和 benchmark 测试。"""

    def test_cache_hit_returns_cached(self) -> None:
        """缓存命中时不重新 benchmark。"""
        factory = DataProviderFactory()
        factory._write_ranked_chain("sh", ["baostock", "akshare"])
        result = factory.get_ranked_chain_for_ticker("sh.600519")
        assert result == ["baostock", "akshare"]

    def test_different_ticker_type_independent(self) -> None:
        """不同 ticker 类型（sh/sz）缓存独立。"""
        factory = DataProviderFactory(primary="akshare", fallbacks=["baostock"])
        factory._write_ranked_chain("sh", ["baostock", "akshare"])
        # sz 没有缓存，用 mock 避免真实网络调用
        # bench_all 返回已按延迟排序的结果，mock 直接给出正确顺序
        with patch.object(
            factory,
            "bench_all",
            return_value=[
                _BenchResult(name="baostock", latency_ms=50.0, success=True),
                _BenchResult(name="akshare", latency_ms=80.0, success=True),
            ],
        ):
            result_sz = factory.get_ranked_chain_for_ticker("sz.000001")
        assert result_sz == ["baostock", "akshare"]
        # sh 的缓存不受影响
        assert factory._get_cached_ranked_chain("sh")[0] == ["baostock", "akshare"]


class TestProviderChainForTicker:
    """_provider_chain_for_ticker 合并逻辑测试。"""

    def test_bj_ticker_forces_tonghuashun_first(self) -> None:
        """北交所 ticker 强制 tonghuashun 置顶。"""
        chain = DataProviderFactory._provider_chain_for_ticker("bj.920001")
        assert chain[0] == "tonghuashun"

    def test_sh_ticker_uses_benchmark_cache(self) -> None:
        """Sh ticker 使用缓存的 benchmark 结果。"""
        factory = DataProviderFactory()
        factory._write_ranked_chain("sh", ["akshare", "baostock", "mootdx"])
        chain = DataProviderFactory._provider_chain_for_ticker("sh.600519")
        # akshare 应排在 baostock 前面
        assert chain.index("akshare") < chain.index("baostock")

    def test_sh_ticker_fallback_to_fixed_order_when_no_cache(self) -> None:
        """无缓存时回退到固定顺序。"""
        # 确保缓存为空
        DataProviderFactory._rank_cache.clear()
        chain = DataProviderFactory._provider_chain_for_ticker("sh.600519")
        # 默认顺序：baostock → akshare → mootdx → tushare → tonghuashun
        assert chain[0] == "baostock"
        assert chain[1] == "akshare"

    def test_cached_result_filtered_to_base_chain(self) -> None:
        """缓存中的 provider 若不在 base_chain 中则被过滤掉。"""
        factory = DataProviderFactory(primary="akshare", fallbacks=["baostock"])
        # 写入一个包含 tonghuashun 的缓存（但 base_chain 不含 tonghuashun）
        factory._write_ranked_chain("sh", ["tonghuashun", "baostock", "akshare"])
        chain = DataProviderFactory._provider_chain_for_ticker("sh.600519")
        # tonghuashun 不在 base_chain 中，应被排除
        assert "tonghuashun" not in chain
        # 默认 factory primary 是 baostock，base_chain = [baostock, akshare]
        # filtered = [baostock, akshare]（按缓存顺序保留 base_chain 中的）
        # 由于缓存中 baostock 在 akshare 前，结果应保持此顺序
        assert chain.index("baostock") < chain.index("akshare")


class TestResetDataFactory:
    """reset_data_factory 清理 benchmark 缓存测试。"""

    def test_clears_rank_cache(self) -> None:
        factory = DataProviderFactory()
        factory._write_ranked_chain("sh", ["baostock"])
        reset_data_factory()
        # 缓存应被清空
        assert "sh" not in DataProviderFactory._rank_cache


class TestInvalidateRankCache:
    """invalidate_rank_cache 方法测试。"""

    def test_removes_cached_chain(self) -> None:
        factory = DataProviderFactory()
        factory._write_ranked_chain("sh", ["baostock", "akshare"])
        assert factory._get_cached_ranked_chain("sh") is not None
        factory.invalidate_rank_cache("sh")
        assert factory._get_cached_ranked_chain("sh") is None

    def test_noop_on_missing_key(self) -> None:
        """清除不存在的 key 不报错。"""
        factory = DataProviderFactory()
        factory.invalidate_rank_cache("nonexistent")  # 不应抛出异常

    def test_only_clears_requested_type(self) -> None:
        """只清除指定的 ticker 类型，不影响其他类型。"""
        factory = DataProviderFactory()
        factory._write_ranked_chain("sh", ["baostock"])
        factory._write_ranked_chain("sz", ["akshare"])
        factory.invalidate_rank_cache("sh")
        assert factory._get_cached_ranked_chain("sh") is None
        cached_sz = factory._get_cached_ranked_chain("sz")
        assert cached_sz is not None
        assert cached_sz[0] == ["akshare"]
