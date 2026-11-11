"""tests for trade_krono_cli.data_providers.tencent_provider (腾讯财经数据源)."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

import trade_krono_cli.data_providers.tencent_provider as tp
from trade_krono_cli.data_providers.tencent_provider import TencentProvider


def _minute_row(
    ts: str, open_: float, close: float, high: float, low: float, volume: float, turn: float
) -> list:
    """构造腾讯分钟 K 线行（[datetime, open, close, high, low, volume, turnover_obj, turnover]）。"""
    return [ts, str(open_), str(close), str(high), str(low), str(volume), {}, str(turn)]


class TestTencentKline:
    """K 线拉取：分钟线 / 日线 / 边界。"""

    def test_fetch_kline_minutes(self) -> None:
        provider = TencentProvider()
        rows = [_minute_row("202610271455", 1356.90, 1356.17, 1356.98, 1354.48, 2041.60, 1.6332)]
        with patch.object(
            tp.TencentProvider, "_tencent_kline_call", return_value={"sh600519": {"m5": rows}}
        ):
            kline = provider.fetch_kline("sh.600519", "2026-10-27", "2026-10-28", frequency="5min")

        assert kline is not None
        assert kline.length == 1
        assert kline.close[0] == pytest.approx(1356.17)
        assert kline.high[0] == pytest.approx(1356.98)
        assert kline.low[0] == pytest.approx(1354.48)
        assert kline.volume[0] == pytest.approx(2041.6)
        assert kline.amount[0] > 0
        assert kline.timestamps[0] == datetime(2026, 10, 27, 14, 55)

    def test_fetch_kline_unsupported_frequency_returns_none(self) -> None:
        provider = TencentProvider()
        assert (
            provider.fetch_kline("sh.600519", "2026-10-27", "2026-10-28", frequency="yearly")
            is None
        )

    def test_fetch_kline_unsupported_adjust_raises(self) -> None:
        provider = TencentProvider()
        with pytest.raises(ValueError, match="复权"):
            provider.fetch_kline("sh.600519", "2026-10-27", "2026-10-28", adjustflag="3")

    def test_fetch_kline_bj_rejected(self) -> None:
        provider = TencentProvider()
        with pytest.raises(ValueError, match="北交所"):
            provider.fetch_kline("bj.920080", "2026-10-27", "2026-10-28")

    def test_fetch_kline_bad_symbol_raises(self) -> None:
        provider = TencentProvider()
        with pytest.raises(ValueError, match="无法解析"):
            provider.fetch_kline("not-a-ticker", "2026-10-27", "2026-10-28")


class TestTencentQuote:
    """行情拉取：字段映射 / is_stale。"""

    def _fake_gbk_response(self) -> str:
        value = (
            "1~贵州茅台~600519~1439.00~1444.99~1438.23~23038~11782~11255~1439.00~7~1438.50~1~1438.40~1~1438.26~1~1438.18~"
            "1~1439.01~1~1439.27~1~1439.93~1~1439.99~1~1440.00~50~~20261110161453~-5.99~-0.41~1444.50~1431.26~1439.00/23038/3316078849"
            "~23038~331608~0.18~21.89~~1444.50~1431.26~0.92~17988.67~17988.67~6.63~1589.49~1300.49~0.45~-43~1439.41~20.93~21.85~~~"
            "-0.03~331607.8849~71.9500~5~   A~GP-A~6.66~-2.77~3.62~30.29~26.74~1539.98~1151.01~6.13~12.87~6.68~1250081601~1250081601~"
            "-66.15~-3.23~1250081601~~~2.03~-0.29~~CNY~0~___D__F__N~1438.00~17~"
        )
        return f'v_sh600519="{value}";'

    def test_fetch_quote_maps_fields(self) -> None:
        provider = TencentProvider()
        with patch.object(tp, "_tencent_http_get", return_value=self._fake_gbk_response()):
            quote = provider.fetch_quote("sh.600519")

        assert quote is not None
        assert quote.ticker == "sh.600519"
        assert quote.source == "tencent"
        assert quote.price == 1439.00
        assert quote.pe is not None
        assert quote.pb is not None
        assert quote.market_cap is not None
        assert quote.turnover is not None

    def test_fetch_quote_propagates_source_error(self) -> None:
        """数据源异常向上抛（来源格式 / 网络错误），由工厂统一降级处理。"""
        provider = TencentProvider()
        with (
            patch.object(tp, "_tencent_http_get", side_effect=RuntimeError("network down")),
            pytest.raises(RuntimeError, match="network down"),
        ):
            provider.fetch_quote("sh.600519")

    def test_health_check_ok(self) -> None:
        provider = TencentProvider()
        with patch.object(tp.TencentProvider, "fetch_quote", return_value=MagicMock()):
            assert provider.health_check() is True

    def test_health_check_fail(self) -> None:
        provider = TencentProvider()
        with patch.object(tp.TencentProvider, "fetch_quote", side_effect=RuntimeError("down")):
            assert provider.health_check() is False


class TestTencentSymbols:
    """ticker 归一化（sh. / sz. / bj. / 裸码 → 腾讯代码）。"""

    def test_to_tencent_symbol_sh(self) -> None:
        assert TencentProvider()._to_tencent_symbol("sh.600519") == "sh600519"

    def test_to_tencent_symbol_sz(self) -> None:
        assert TencentProvider()._to_tencent_symbol("sz.000858") == "sz000858"

    def test_to_tencent_symbol_bj(self) -> None:
        assert TencentProvider()._to_tencent_symbol("bj.920080") == "bj920080"

    def test_to_tencent_symbol_bare_etf(self) -> None:
        assert TencentProvider()._to_tencent_symbol("510300") == "sh510300"
        assert TencentProvider()._to_tencent_symbol("159915") == "sz159915"


class TestTencentKlineCall:
    """K 线三入口冷却逻辑。"""

    def test_kline_call_raises_when_all_hosts_fail(self) -> None:
        provider = TencentProvider()
        with (
            patch.object(
                tp,
                "_tencent_http_get",
                side_effect=RuntimeError("boom"),
            ),
            pytest.raises(RuntimeError, match="全部入口不可用"),
        ):
            provider._tencent_kline_call("/appstock/app/kline/mkline", "sh600519,m5,,,5")
