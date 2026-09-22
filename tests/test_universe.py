"""Tests for universe engine — multi-stage A-share universe discovery."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from trade_krono_cli.configs.filters import FilterConfig
from trade_krono_cli.stock_filter import MaxValueRule, MinValueRule
from trade_krono_cli.universe.engine import UniverseEngine, get_universe
from trade_krono_cli.universe.provider import (
    AkshareUniverseProvider,
    UniverseProvider,
    UniverseTicket,
    get_universe_provider,
)
from trade_krono_cli.universe.stages.factor import FactorFilterStage
from trade_krono_cli.universe.stages.fundamental import FundamentalFilterStage
from trade_krono_cli.universe.stages.rules import FilterRulesStage
from trade_krono_cli.universe.stages.static import StaticFilterStage

# ═══════════════════════════════════════════════════════
# Provider tests
# ═══════════════════════════════════════════════════════


class TestUniverseTicket:
    def test_basic(self) -> None:
        t = UniverseTicket(ticker="sh.600519", name="贵州茅台")
        assert t.ticker == "sh.600519"
        assert t.pe is None

    def test_with_data(self) -> None:
        t = UniverseTicket(
            ticker="sz.000858",
            name="五粮液",
            price=150.0,
            pe=25.0,
            pb=5.0,
            market_cap=6000.0,
            volume_ratio=1.2,
            turnover_rate=0.8,
        )
        assert t.market_cap == 6000.0
        assert t.volume_ratio == 1.2


class TestAkshareUniverseProvider:
    def test_code_to_ticker_shanghai(self) -> None:
        assert AkshareUniverseProvider._code_to_ticker("600519") == "sh.600519"
        assert AkshareUniverseProvider._code_to_ticker("510000") == "sh.510000"
        assert AkshareUniverseProvider._code_to_ticker("920000") == "sh.920000"

    def test_code_to_ticker_shenzhen(self) -> None:
        assert AkshareUniverseProvider._code_to_ticker("000858") == "sz.000858"
        assert AkshareUniverseProvider._code_to_ticker("300750") == "sz.300750"
        assert AkshareUniverseProvider._code_to_ticker("002594") == "sz.002594"

    def test_safe_float_none(self) -> None:
        assert UniverseProvider._safe_float(None) is None

    def test_safe_float_nan(self) -> None:
        assert UniverseProvider._safe_float(float("nan")) is None

    def test_safe_float_inf(self) -> None:
        assert UniverseProvider._safe_float(float("inf")) is None
        assert UniverseProvider._safe_float(float("-inf")) is None

    def test_safe_float_valid(self) -> None:
        assert UniverseProvider._safe_float("25.5") == 25.5
        assert UniverseProvider._safe_float(42) == 42.0

    @patch.object(AkshareUniverseProvider, "get_universe")
    def test_health_check_true(self, mock_get) -> None:
        mock_get.return_value = [
            UniverseTicket(ticker="sh.600519"),
            UniverseTicket(ticker="sz.000858"),
        ]
        p = AkshareUniverseProvider()
        assert p.health_check() is True

    @patch.object(AkshareUniverseProvider, "get_universe")
    def test_health_check_false(self, mock_get) -> None:
        mock_get.return_value = []
        p = AkshareUniverseProvider()
        assert p.health_check() is False

    @patch.object(AkshareUniverseProvider, "get_universe")
    def test_health_check_exception(self, mock_get) -> None:
        mock_get.side_effect = RuntimeError("network down")
        p = AkshareUniverseProvider()
        assert p.health_check() is False

    def test_get_universe_import_error(self) -> None:
        """Akshare 未安装时返回空列表。"""
        import sys

        with pytest.MonkeyPatch().context() as mp:
            mp.delitem(sys.modules, "akshare", raising=False)
            p = AkshareUniverseProvider()
            result = p.get_universe()
            assert result == []

    def test_get_universe_runtime_error(self) -> None:
        """Akshare 抛出异常时返回空列表。"""
        import sys
        import types

        fake_ak = types.ModuleType("akshare")
        fake_ak.stock_zh_a_spot_em = lambda: (_ for _ in []).throw(RuntimeError("timeout"))
        with pytest.MonkeyPatch().context() as mp:
            mp.setitem(sys.modules, "akshare", fake_ak)
            p = AkshareUniverseProvider()
            result = p.get_universe()
            assert result == []


class TestGetUniverseProvider:
    def test_akshare(self) -> None:
        p = get_universe_provider("akshare")
        assert isinstance(p, AkshareUniverseProvider)

    def test_unknown_falls_back_to_akshare(self) -> None:
        p = get_universe_provider("unknown_source")
        assert isinstance(p, AkshareUniverseProvider)

    def test_empty_source_falls_back_to_akshare(self) -> None:
        p = get_universe_provider("")
        assert isinstance(p, AkshareUniverseProvider)


# ═══════════════════════════════════════════════════════
# Stage tests
# ═══════════════════════════════════════════════════════


def _make_tickets(n: int = 10, **kwargs) -> list[UniverseTicket]:
    return [
        UniverseTicket(
            ticker=f"sh.{600000 + i}",
            pe=kwargs.get("pe", 20.0 + i),
            pb=kwargs.get("pb", 2.0 + i * 0.1),
            market_cap=kwargs.get("market_cap", 100.0 + i * 50),
            volume_ratio=kwargs.get("volume_ratio", 1.0 + i * 0.1),
            turnover_rate=kwargs.get("turnover_rate", 0.5 + i * 0.05),
        )
        for i in range(n)
    ]


class TestStaticFilterStage:
    def test_empty_input(self) -> None:
        stage = StaticFilterStage()
        assert stage.filter([]) == []

    def test_no_filters(self) -> None:
        """所有过滤选项关闭时，所有 tickets 通过。"""
        stage = StaticFilterStage(
            exclude_st=False,
            skip_suspended=False,
            skip_new_stock=False,
        )
        tickets = _make_tickets(5)
        result = stage.filter(tickets)
        assert len(result) == 5

    def test_exclude_low_price(self) -> None:
        """低价股（price < threshold）被排除。"""
        tickets = [
            UniverseTicket(ticker="sh.600001", price=2.0),
            UniverseTicket(ticker="sh.600002", price=50.0),
            UniverseTicket(ticker="sh.600003", price=1.5),
            UniverseTicket(ticker="sh.600004", price=None),  # None 不拦截
        ]
        stage = StaticFilterStage(
            exclude_st=False,
            skip_suspended=False,
            skip_new_stock=False,
            exclude_low_price=True,
            low_price_threshold=3.0,
        )
        with patch("trade_krono_cli.universe.stages.static.precheck_stock_status") as mock_precheck:
            mock_precheck.side_effect = Exception("mock error")
            result = stage.filter(tickets)
        # precheck fails, but low-price filter still applies
        assert len(result) == 2
        assert result[0].ticker == "sh.600002"
        assert result[1].ticker == "sh.600004"

    def test_exclude_low_price_disabled(self) -> None:
        """exclude_low_price=False 时不过滤低价股。"""
        tickets = [
            UniverseTicket(ticker="sh.600001", price=2.0),
            UniverseTicket(ticker="sh.600002", price=50.0),
        ]
        stage = StaticFilterStage(
            exclude_st=False,
            skip_suspended=False,
            skip_new_stock=False,
            exclude_low_price=False,
        )
        with patch("trade_krono_cli.universe.stages.static.precheck_stock_status") as mock_precheck:
            mock_precheck.return_value = {}
            result = stage.filter(tickets)
        assert len(result) == 2


class TestFundamentalFilterStage:
    def test_empty_input(self) -> None:
        stage = FundamentalFilterStage()
        assert stage.filter([]) == []

    def test_market_cap_filter(self) -> None:
        stage = FundamentalFilterStage(market_cap_range=(200.0, 500.0))
        tickets = _make_tickets(5)
        result = stage.filter(tickets)
        # Only tickets with market_cap in [200, 500] should pass
        for t in result:
            assert 200.0 <= t.market_cap <= 500.0

    def test_pe_filter_excludes_negative(self) -> None:
        """PE <= 0 的亏损股被排除。"""
        tickets = [
            UniverseTicket(ticker="sh.600001", pe=-5.0, market_cap=100.0),
            UniverseTicket(ticker="sh.600002", pe=20.0, market_cap=200.0),
        ]
        stage = FundamentalFilterStage(pe_range=(5.0, 50.0))
        result = stage.filter(tickets)
        assert len(result) == 1
        assert result[0].ticker == "sh.600002"

    def test_pe_filter_range(self) -> None:
        tickets = [
            UniverseTicket(ticker="sh.600001", pe=3.0, market_cap=100.0),
            UniverseTicket(ticker="sh.600002", pe=25.0, market_cap=200.0),
            UniverseTicket(ticker="sh.600003", pe=80.0, market_cap=300.0),
        ]
        stage = FundamentalFilterStage(pe_range=(5.0, 50.0))
        result = stage.filter(tickets)
        assert len(result) == 1
        assert result[0].ticker == "sh.600002"

    def test_pb_filter(self) -> None:
        tickets = [
            UniverseTicket(ticker="sh.600001", pb=0.5, market_cap=100.0),
            UniverseTicket(ticker="sh.600002", pb=3.0, market_cap=200.0),
            UniverseTicket(ticker="sh.600003", pb=8.0, market_cap=300.0),
        ]
        stage = FundamentalFilterStage(pb_range=(1.0, 5.0))
        result = stage.filter(tickets)
        assert len(result) == 1
        assert result[0].ticker == "sh.600002"

    def test_no_filters_all_pass(self) -> None:
        stage = FundamentalFilterStage()
        tickets = _make_tickets(3)
        result = stage.filter(tickets)
        assert len(result) == 3

    def test_min_pb_filter(self) -> None:
        """PB < min_pb 的股票被排除（资不抵债风险）。"""
        tickets = [
            UniverseTicket(ticker="sh.600001", pb=0.5, market_cap=100.0),
            UniverseTicket(ticker="sh.600002", pb=1.5, market_cap=200.0),
            UniverseTicket(ticker="sh.600003", pb=3.0, market_cap=300.0),
        ]
        stage = FundamentalFilterStage(min_pb=1.0)
        result = stage.filter(tickets)
        assert len(result) == 2
        assert all(t.ticker != "sh.600001" for t in result)

    def test_min_pb_none_ignores(self) -> None:
        """min_pb=None 时不做 PB 下限过滤。"""
        tickets = [
            UniverseTicket(ticker="sh.600001", pb=0.3, market_cap=100.0),
            UniverseTicket(ticker="sh.600002", pb=2.0, market_cap=200.0),
        ]
        stage = FundamentalFilterStage(min_pb=None)
        result = stage.filter(tickets)
        assert len(result) == 2

    def test_industry_whitelist(self) -> None:
        """industry_whitelist 精确匹配过滤。"""
        tickets = [
            UniverseTicket(ticker="sh.600001", industry="银行"),
            UniverseTicket(ticker="sh.600002", industry="房地产"),
            UniverseTicket(ticker="sh.600003", industry="银行"),
        ]
        stage = FundamentalFilterStage(industry_whitelist=["银行"])
        result = stage.filter(tickets)
        assert len(result) == 2
        assert all(t.industry == "银行" for t in result)

    def test_industry_blacklist(self) -> None:
        """industry_blacklist 排除指定行业。"""
        tickets = [
            UniverseTicket(ticker="sh.600001", industry="银行"),
            UniverseTicket(ticker="sh.600002", industry="房地产"),
            UniverseTicket(ticker="sh.600003", industry="煤炭"),
        ]
        stage = FundamentalFilterStage(industry_blacklist=["房地产", "煤炭"])
        result = stage.filter(tickets)
        assert len(result) == 1
        assert result[0].ticker == "sh.600001"

    def test_industry_none_skips_filter(self) -> None:
        """industry=None 时，whitelist/blacklist 不过滤（宽松策略）。"""
        tickets = [
            UniverseTicket(ticker="sh.600001", industry="银行"),
            UniverseTicket(ticker="sh.600002", industry=None),
            UniverseTicket(ticker="sh.600003", industry="房地产"),
        ]
        stage = FundamentalFilterStage(industry_whitelist=["银行"])
        result = stage.filter(tickets)
        # industry=None 不匹配 whitelist，但也不强制排除（宽松）
        assert len(result) == 2
        assert result[0].ticker == "sh.600001"
        assert result[1].ticker == "sh.600002"

    def test_industry_combined_with_pe_filter(self) -> None:
        """Industry 过滤与 PE 过滤联合生效。"""
        tickets = [
            UniverseTicket(ticker="sh.600001", industry="银行", pe=8.0),
            UniverseTicket(ticker="sh.600002", industry="房地产", pe=15.0),
            UniverseTicket(ticker="sh.600003", industry="银行", pe=60.0),
        ]
        stage = FundamentalFilterStage(
            industry_whitelist=["银行"],
            pe_range=(5.0, 50.0),
        )
        result = stage.filter(tickets)
        assert len(result) == 1
        assert result[0].ticker == "sh.600001"


class TestFactorFilterStage:
    def test_empty_input(self) -> None:
        stage = FactorFilterStage()
        assert stage.filter([]) == []

    def test_volume_ratio_filter(self) -> None:
        tickets = [
            UniverseTicket(ticker="sh.600001", volume_ratio=0.5),
            UniverseTicket(ticker="sh.600002", volume_ratio=1.5),
            UniverseTicket(ticker="sh.600003", volume_ratio=2.0),
        ]
        stage = FactorFilterStage(min_volume_ratio=1.0)
        result = stage.filter(tickets)
        assert len(result) == 2
        assert all(t.volume_ratio >= 1.0 for t in result)

    def test_turnover_rate_filter(self) -> None:
        tickets = [
            UniverseTicket(ticker="sh.600001", turnover_rate=0.1),
            UniverseTicket(ticker="sh.600002", turnover_rate=0.8),
            UniverseTicket(ticker="sh.600003", turnover_rate=1.5),
        ]
        stage = FactorFilterStage(min_turnover_rate=0.5)
        result = stage.filter(tickets)
        assert len(result) == 2

    def test_combined_filters(self) -> None:
        tickets = [
            UniverseTicket(ticker="sh.600001", volume_ratio=0.5, turnover_rate=0.1),
            UniverseTicket(ticker="sh.600002", volume_ratio=1.5, turnover_rate=0.8),
            UniverseTicket(ticker="sh.600003", volume_ratio=2.0, turnover_rate=0.3),
        ]
        stage = FactorFilterStage(min_volume_ratio=1.0, min_turnover_rate=0.5)
        result = stage.filter(tickets)
        assert len(result) == 1
        assert result[0].ticker == "sh.600002"

    def test_no_filters_all_pass(self) -> None:
        stage = FactorFilterStage()
        tickets = _make_tickets(3)
        result = stage.filter(tickets)
        assert len(result) == 3


# ═══════════════════════════════════════════════════════
# Engine tests
# ═══════════════════════════════════════════════════════


class TestUniverseEngine:
    def test_from_config(self) -> None:
        fc = FilterConfig(universe_source="akshare")
        engine = UniverseEngine.from_config(fc)
        assert engine is not None
        assert len(engine.stage_summary()) == 3

    def test_run_with_mock_provider(self) -> None:
        mock_provider = MagicMock(spec=UniverseProvider)
        mock_provider.name = "mock"
        mock_provider.get_universe.return_value = _make_tickets(5)

        stages = [
            FundamentalFilterStage(market_cap_range=(0, 10000)),
            FactorFilterStage(),
        ]
        engine = UniverseEngine(
            provider=mock_provider,
            stages=stages,
            cache_dir=MagicMock(),
        )
        # Disable cache by mocking path methods
        with patch.object(engine, "_cache_dir"):
            engine._cache_dir.exists = lambda: False
            engine._cache_dir.mkdir = lambda **kw: None
            tickers = engine.run(eval_date="2026-08-13")
        assert len(tickers) == 5
        assert all(isinstance(t, str) for t in tickers)

    def test_run_returns_empty_when_provider_empty(self) -> None:
        mock_provider = MagicMock(spec=UniverseProvider)
        mock_provider.name = "mock"
        mock_provider.get_universe.return_value = []

        engine = UniverseEngine(
            provider=mock_provider,
            stages=[],
            cache_dir=MagicMock(),
        )
        with patch.object(engine, "_cache_dir"):
            engine._cache_dir.exists = lambda: False
            engine._cache_dir.mkdir = lambda **kw: None
            tickers = engine.run()
        assert tickers == []

    def test_stage_summary(self) -> None:
        fc = FilterConfig(universe_source="akshare")
        engine = UniverseEngine.from_config(fc)
        stages = engine.stage_summary()
        assert len(stages) == 3
        names = [s["name"] for s in stages]
        assert "static" in names
        assert "fundamental" in names
        assert "factor" in names

    def test_stage_summary_with_rules(self) -> None:
        """含 filter_rules 时 stages 包含 rules。"""
        from trade_krono_cli.stock_filter import MinValueRule

        fc = FilterConfig(
            universe_source="akshare",
            filter_rules=[MinValueRule("price", 3.0)],
        )
        engine = UniverseEngine.from_config(fc)
        names = [s["name"] for s in engine.stage_summary()]
        assert "rules" in names

    def test_cache_key_deterministic(self) -> None:
        mock_provider = MagicMock(spec=UniverseProvider)
        mock_provider.name = "mock"
        engine = UniverseEngine(
            provider=mock_provider,
            stages=[],
            cache_dir=MagicMock(),
        )
        key1 = engine._cache_key("2026-08-13")
        key2 = engine._cache_key("2026-08-13")
        assert key1 == key2
        assert len(key1) == 16  # 16-char hex

    def test_cache_key_differs_by_date(self) -> None:
        mock_provider = MagicMock(spec=UniverseProvider)
        mock_provider.name = "mock"
        engine = UniverseEngine(
            provider=mock_provider,
            stages=[],
            cache_dir=MagicMock(),
        )
        key_a = engine._cache_key("2026-08-13")
        key_b = engine._cache_key("2026-08-14")
        assert key_a != key_b

    def test_cache_requires_refresh_no_market_cap(self) -> None:
        """无 market_cap_min 等参数时，不要求刷新缓存。"""
        mock_provider = MagicMock(spec=UniverseProvider)
        mock_provider.name = "mock"
        stages = [StaticFilterStage()]
        engine = UniverseEngine(
            provider=mock_provider,
            stages=stages,
            cache_dir=MagicMock(),
        )
        assert engine._cache_requires_refresh() is False

    def test_cache_requires_refresh_all_attrs_none(self) -> None:
        """所有属性均为 None 时不要求刷新（覆盖 _cache_requires_refresh 全部 False 分支）。"""
        from types import SimpleNamespace

        mock_provider = MagicMock(spec=UniverseProvider)
        mock_provider.name = "mock"
        # Stage with all filter attrs set to None → all hasattr checks hit but all are None
        stage = SimpleNamespace(
            name="dummy",
            market_cap_min=None,
            pe_range=None,
            pb_range=None,
            min_pb=None,
            min_volume=None,
            min_volume_ratio=None,
            min_turnover_rate=None,
        )
        engine = UniverseEngine(
            provider=mock_provider,
            stages=[stage],
            cache_dir=MagicMock(),
        )
        assert engine._cache_requires_refresh() is False

    def test_cache_requires_refresh_with_market_cap(self) -> None:
        """含 market_cap_min 时要求刷新缓存。"""
        mock_provider = MagicMock(spec=UniverseProvider)
        mock_provider.name = "mock"
        stages = [
            StaticFilterStage(),
            FundamentalFilterStage(market_cap_min=100.0),
        ]
        engine = UniverseEngine(
            provider=mock_provider,
            stages=stages,
            cache_dir=MagicMock(),
        )
        assert engine._cache_requires_refresh() is True

    def test_load_cache_miss(self, tmp_path) -> None:
        """缓存文件不存在时返回 None。"""
        mock_provider = MagicMock(spec=UniverseProvider)
        mock_provider.name = "mock"
        engine = UniverseEngine(
            provider=mock_provider,
            stages=[],
            cache_dir=tmp_path,
        )
        result = engine._load_cache("nonexistent_key", "2026-08-13")
        assert result is None

    def test_save_and_load_cache(self, tmp_path) -> None:
        """写入缓存后可正确读取。"""
        mock_provider = MagicMock(spec=UniverseProvider)
        mock_provider.name = "mock"
        engine = UniverseEngine(
            provider=mock_provider,
            stages=[],
            cache_dir=tmp_path,
            cache_ttl_hours=24,
        )
        tickers = ["sh.600519", "sz.000858"]
        engine._save_cache("test_key", tickers, "2026-08-13")
        loaded = engine._load_cache("test_key", "2026-08-13")
        assert loaded == tickers

    def test_run_with_cache_hit(self, tmp_path) -> None:
        """缓存命中时直接返回，不执行 provider 调用。"""
        mock_provider = MagicMock(spec=UniverseProvider)
        mock_provider.name = "mock"
        mock_provider.get_universe.return_value = _make_tickets(5)

        engine = UniverseEngine(
            provider=mock_provider,
            stages=[],
            cache_dir=tmp_path,
            cache_ttl_hours=24,
        )
        # Pre-populate cache
        engine._save_cache("cached_key", ["sh.600519"], "2026-08-13")
        with patch.object(engine, "_cache_key", return_value="cached_key"):
            result = engine.run(eval_date="2026-08-13")
        assert result == ["sh.600519"]
        mock_provider.get_universe.assert_not_called()

    def test_run_with_expired_cache(self, tmp_path) -> None:
        """缓存过期时跳过缓存，重新执行。"""
        mock_provider = MagicMock(spec=UniverseProvider)
        mock_provider.name = "mock"
        mock_provider.get_universe.return_value = _make_tickets(3)

        engine = UniverseEngine(
            provider=mock_provider,
            stages=[],
            cache_dir=tmp_path,
            cache_ttl_hours=0,  # 立即过期
        )
        # Write cache with old timestamp
        engine._save_cache("cached_key", ["sh.600519"], "2026-08-13")
        with patch.object(engine, "_cache_key", return_value="cached_key"):
            result = engine.run(eval_date="2026-08-13")
        assert len(result) == 3
        mock_provider.get_universe.assert_called_once()

    def test_from_config_provider_unavailable(self) -> None:
        """provider 全不可用时 from_config 抛出 ValueError。"""
        with patch("trade_krono_cli.universe.engine.get_universe_provider") as mock_get:
            mock_get.return_value = None
            fc = FilterConfig(universe_source="nonexistent")
            with pytest.raises(ValueError, match="无法初始化 UniverseProvider"):
                UniverseEngine.from_config(fc, universe_source="nonexistent")

    def test_run_requires_refresh_no_cache(self, tmp_path) -> None:
        """cache_requires_refresh=True 且无缓存时执行完整管道。"""
        mock_provider = MagicMock(spec=UniverseProvider)
        mock_provider.name = "mock"
        mock_provider.get_universe.return_value = _make_tickets(4)

        stages = [
            StaticFilterStage(),
            FundamentalFilterStage(market_cap_min=100.0),  # triggers refresh
        ]
        engine = UniverseEngine(
            provider=mock_provider,
            stages=stages,
            cache_dir=tmp_path,
        )
        result = engine.run(eval_date="2026-08-13")
        assert len(result) == 4
        mock_provider.get_universe.assert_called_once()

    def test_run_stage_filters_to_empty(self, tmp_path) -> None:
        """某阶段过滤后结果为空时提前终止。"""
        mock_provider = MagicMock(spec=UniverseProvider)
        mock_provider.name = "mock"
        mock_provider.get_universe.return_value = _make_tickets(5)

        stages = [
            StaticFilterStage(),
            FundamentalFilterStage(market_cap_range=(0, 1)),  # 过滤掉所有票
        ]
        engine = UniverseEngine(
            provider=mock_provider,
            stages=stages,
            cache_dir=tmp_path,
        )
        result = engine.run(eval_date="2026-08-13")
        assert result == []

    def test_save_cache_exception_handled(self, tmp_path) -> None:
        """缓存写入异常时不报错（只记 debug 日志）。"""
        mock_provider = MagicMock(spec=UniverseProvider)
        mock_provider.name = "mock"
        engine = UniverseEngine(
            provider=mock_provider,
            stages=[],
            cache_dir=tmp_path / "unwriteable",
        )
        # unwriteable 目录不存在，但 mkdir 会成功；用 readonly 路径模拟
        import os
        readonly_dir = tmp_path / "readonly"
        readonly_dir.mkdir()
        os.chmod(readonly_dir, 0o555)
        try:
            engine._cache_dir = readonly_dir
            engine._save_cache("key", ["sh.600519"], "2026-08-13")
            # 不应抛异常
        finally:
            os.chmod(readonly_dir, 0o755)
    def test_returns_list_of_strings(self) -> None:
        mock_provider = MagicMock(spec=UniverseProvider)
        mock_provider.name = "mock"
        mock_provider.get_universe.return_value = _make_tickets(3)

        with patch("trade_krono_cli.universe.engine.get_universe_provider") as mock_factory:
            mock_factory.return_value = mock_provider
            fc = FilterConfig(universe_source="mock")
            tickers = get_universe(fc, universe_source="mock", eval_date="2026-08-13")
        assert len(tickers) == 3
        assert all(isinstance(t, str) for t in tickers)


class TestFilterRulesStage:
    def test_empty_input(self) -> None:
        stage = FilterRulesStage()
        assert stage.filter([]) == []

    def test_no_rules(self) -> None:
        """无规则时所有 tickets 通过。"""
        stage = FilterRulesStage(rules=[])
        tickets = [
            UniverseTicket(ticker="sh.600001", price=2.0),
            UniverseTicket(ticker="sh.600002", price=50.0),
        ]
        assert len(stage.filter(tickets)) == 2

    def test_min_price_rule(self) -> None:
        """Price >= 5.0 的规则。"""
        tickets = [
            UniverseTicket(ticker="sh.600001", price=2.0),
            UniverseTicket(ticker="sh.600002", price=50.0),
            UniverseTicket(ticker="sh.600003", price=5.0),
        ]
        rules = [MinValueRule("price", 5.0)]
        stage = FilterRulesStage(rules=rules)
        result = stage.filter(tickets)
        assert len(result) == 2
        assert result[0].ticker == "sh.600002"
        assert result[1].ticker == "sh.600003"

    def test_max_pe_rule(self) -> None:
        """Pe <= 50 的规则（PE 别名映射）。"""
        tickets = [
            UniverseTicket(ticker="sh.600001", pe=10.0),
            UniverseTicket(ticker="sh.600002", pe=80.0),
            UniverseTicket(ticker="sh.600003", pe=50.0),
        ]
        rules = [MaxValueRule("pe", 50.0)]
        stage = FilterRulesStage(rules=rules)
        result = stage.filter(tickets)
        assert len(result) == 2

    def test_none_field_skips_rule(self) -> None:
        """price=None 时跳过依赖 price 的规则。"""
        tickets = [
            UniverseTicket(ticker="sh.600001", price=None),
            UniverseTicket(ticker="sh.600002", price=50.0),
        ]
        rules = [MinValueRule("price", 5.0)]
        stage = FilterRulesStage(rules=rules)
        result = stage.filter(tickets)
        assert len(result) == 2

    def test_market_cap_billion_alias(self) -> None:
        """market_cap_billion 别名映射到 market_cap。"""
        tickets = [
            UniverseTicket(ticker="sh.600001", market_cap=10.0),
            UniverseTicket(ticker="sh.600002", market_cap=100.0),
        ]
        rules = [MinValueRule("market_cap_billion", 50.0)]
        stage = FilterRulesStage(rules=rules)
        result = stage.filter(tickets)
        assert len(result) == 1
        assert result[0].ticker == "sh.600002"
