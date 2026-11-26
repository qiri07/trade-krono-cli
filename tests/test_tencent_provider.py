"""tests for trade_krono_cli.data_providers.tencent_provider (腾讯财经数据源)."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

import trade_krono_cli.data_providers.tencent_provider as tp
from trade_krono_cli.data_providers.tencent_provider import TencentProvider


def _minute_row(
    ts: str,
    open_: float,
    close: float,
    high: float,
    low: float,
    volume: float,
    turn: float,
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


class TestTencentHttpGet:
    """_tencent_http_get：urllib 请求/响应与异常处理。"""

    def _make_urlopen_mock(self, charset: str | None, body: bytes) -> MagicMock:
        """构造模拟 urlopen 上下文管理器（with ... as response:）。"""
        mock_response = MagicMock()
        mock_response.headers.get_content_charset.return_value = charset
        mock_response.read.return_value = body
        mock_cm = MagicMock()
        mock_cm.__enter__.return_value = mock_response
        return mock_cm

    def test_http_get_success(self) -> None:
        """正常响应返回解码后的文本。"""
        mock_cm = self._make_urlopen_mock("gbk", b"test_data")
        with patch.object(tp.urllib.request, "urlopen", return_value=mock_cm):
            result = tp._tencent_http_get("https://example.com/api")
        assert result == "test_data"

    def test_http_get_with_params(self) -> None:
        """带 params 时 URL 会拼接查询字符串。"""
        mock_cm = self._make_urlopen_mock(None, b"ok")
        with patch.object(tp.urllib.request, "urlopen", return_value=mock_cm):
            tp._tencent_http_get("https://example.com/api", {"a": "1", "b": "2"})

    def test_http_get_failure_raises_runtime_error(self) -> None:
        """urlopen 抛异常时应包装为 RuntimeError。"""
        with patch.object(
            tp.urllib.request, "urlopen", side_effect=OSError("network down")
        ), pytest.raises(RuntimeError, match="tencent 请求"):
            tp._tencent_http_get("https://example.com/api")


class TestTencentNum:
    """_tencent_num：数值转换边界情况。"""

    def test_tencent_num_bool(self) -> None:
        """布尔值返回 None。"""
        assert tp._tencent_num(True) is None
        assert tp._tencent_num(False) is None

    def test_tencent_num_none(self) -> None:
        """None 返回 None。"""
        assert tp._tencent_num(None) is None

    def test_tencent_num_int_float(self) -> None:
        """int / float 直接转换。"""
        assert tp._tencent_num(42) == 42.0
        assert tp._tencent_num(3.14) == 3.14

    def test_tencent_num_string_with_comma(self) -> None:
        """带逗号的字符串可转换。"""
        assert tp._tencent_num("1,234.56") == 1234.56

    def test_tencent_num_empty_dash(self) -> None:
        """空字符串 / 短横线返回 None。"""
        assert tp._tencent_num("") is None
        assert tp._tencent_num("-") is None
        assert tp._tencent_num("--") is None

    def test_tencent_num_invalid_string(self) -> None:
        """非数字字符串返回 None。"""
        assert tp._tencent_num("abc") is None

    def test_tencent_num_nan_inf(self) -> None:
        """NaN / Inf 返回 None。"""
        assert tp._tencent_num(float("nan")) is None
        assert tp._tencent_num(float("inf")) is None


class TestTencentReqNum:
    """_tencent_req_num：必填数值异常。"""

    def test_tencent_req_num_raises(self) -> None:
        """值为 None 时抛 RuntimeError。"""
        with pytest.raises(RuntimeError, match="tencent 来源字段"):
            tp._tencent_req_num(None, "open")


class TestTencentSrcDate:
    """_tencent_src_date：日期格式解析。"""

    def test_tencent_src_date_yyyymmdd(self) -> None:
        """8 位数字日期按 %Y%m%d 解析。"""
        assert tp._tencent_src_date("20261027") == "2026-10-27"

    def test_tencent_src_date_yyyy_mm_dd(self) -> None:
        """标准日期按 %Y-%m-%d 解析。"""
        assert tp._tencent_src_date("2026-10-27") == "2026-10-27"

    def test_tencent_src_date_invalid(self) -> None:
        """无法识别的日期抛 RuntimeError。"""
        with pytest.raises(RuntimeError, match="无法识别的日期"):
            tp._tencent_src_date("not-a-date")


class TestTencentIterQuoteBlocks:
    """_iter_quote_blocks：行情响应拆分边界。"""

    def test_iter_quote_blocks_empty_line(self) -> None:
        """空行被跳过。"""
        blocks = tp._iter_quote_blocks("")
        assert blocks == []

    def test_iter_quote_blocks_no_equals(self) -> None:
        """没有 = 的行被跳过。"""
        blocks = tp._iter_quote_blocks("just some text;no_equals_here")
        assert blocks == []

    def test_iter_quote_blocks_no_quote(self) -> None:
        """没有引号的行被跳过。"""
        blocks = tp._iter_quote_blocks("key=value")
        assert blocks == []

    def test_iter_quote_blocks_empty_key(self) -> None:
        """key 为空时被跳过（line 162）。"""
        data = 'v_="1~2~3";'
        blocks = tp._iter_quote_blocks(data)
        assert blocks == []


class TestTencentQuoteFromValues:
    """_quote_from_values：字段不足返回 None。"""

    def test_quote_from_values_insufficient_fields(self) -> None:
        """字段少于 53 项返回 None（line 178）。"""
        result = tp._quote_from_values(["a", "b", "c"], "sh.600519")
        assert result is None


class TestTencentFetchDaily:
    """_fetch_tencent_daily：日线拉取边界。"""

    def test_fetch_daily_start_after_end_raises(self) -> None:
        """起始日期晚于结束日期抛 ValueError。"""
        provider = TencentProvider()
        with (
            patch.object(provider, "_to_tencent_symbol", return_value="sh600519"),
            patch.object(
                provider, "_tencent_kline_call", return_value={"sh600519": {}}
            ),
            pytest.raises(ValueError, match="起始日期"),
        ):
            provider._fetch_tencent_daily(
                "sh.600519", "day", "1", "2026-10-28", "2026-10-27"
            )

    def test_fetch_daily_zero_rows_raises(self) -> None:
        """返回 0 根 K 线时抛 RuntimeError。"""
        provider = TencentProvider()
        with (
            patch.object(provider, "_to_tencent_symbol", return_value="sh600519"),
            patch.object(
                provider,
                "_tencent_kline_call",
                return_value={"sh600519": {"day": []}},
            ),
            pytest.raises(RuntimeError, match="0 根"),
        ):
            provider._fetch_tencent_daily(
                "sh.600519", "day", "1", "2026-10-27", "2026-10-28"
            )

    def test_fetch_daily_price_zero_raises(self) -> None:
        """价格出现 ≤0 时抛 RuntimeError。"""
        provider = TencentProvider()
        with (
            patch.object(provider, "_to_tencent_symbol", return_value="sh600519"),
            patch.object(
                provider,
                "_tencent_kline_call",
                return_value={"sh600519": {"day": [["20261027", "0", "0", "0", "0", "0"]]}},
            ),
            pytest.raises(RuntimeError, match="≤0"),
        ):
            provider._fetch_tencent_daily(
                "sh.600519", "day", "1", "2026-10-27", "2026-10-28"
            )


class TestTencentFetchMinutes:
    """_fetch_tencent_minutes：分钟线边界。"""

    def test_fetch_minutes_empty_items_raises(self) -> None:
        """返回 0 根分钟线时抛 RuntimeError。"""
        provider = TencentProvider()
        with (
            patch.object(provider, "_to_tencent_symbol", return_value="sh600519"),
            patch.object(
                provider,
                "_tencent_kline_call",
                return_value={"sh600519": {"m5": []}},
            ),
            pytest.raises(RuntimeError, match="返回 0 根"),
        ):
            provider._fetch_tencent_minutes("sh.600519", "m5")


class TestTencentKlineCallEdgeCases:
    """_tencent_kline_call：各失败路径。"""

    def test_kline_call_param_error(self) -> None:
        """来源返回 param error 时抛 ValueError。"""
        provider = TencentProvider()
        with patch.object(
            tp,
            "_tencent_http_get",
            return_value='{"code": -1, "msg": "param error"}',
        ), pytest.raises(ValueError, match="参数错误"):
            provider._tencent_kline_call("/appstock/app/kline/mkline", "sh600519,m5,,,5")

    def test_kline_call_non_dict_payload(self) -> None:
        """payload 不是 dict 时标记入口失败并继续下一入口。"""
        provider = TencentProvider()
        # 两个入口：第一个返回非 dict，第二个也返回非 dict，最终全部不可用
        with patch.object(
            tp,
            "_tencent_http_get",
            side_effect=['"not a dict"', '"not a dict"'],
        ), pytest.raises(RuntimeError, match="全部入口不可用"):
            provider._tencent_kline_call("/appstock/app/kline/mkline", "sh600519,m5,,,5")

    def test_kline_call_empty_data(self) -> None:
        """data 为空 dict 时标记入口失败。"""
        provider = TencentProvider()
        with patch.object(
            tp,
            "_tencent_http_get",
            side_effect=['{"code": 0, "data": {}}', '{"code": 0, "data": {}}'],
        ), pytest.raises(RuntimeError, match="全部入口不可用"):
            provider._tencent_kline_call("/appstock/app/kline/mkline", "sh600519,m5,,,5")


class TestTencentFetchQuote:
    """fetch_quote：未找到匹配返回 None。"""

    def test_fetch_quote_no_match(self) -> None:
        """行情中未找到对应代码时返回 None。"""
        provider = TencentProvider()
        with patch.object(tp, "_tencent_http_get", return_value='v_sz000001="1~平安银行~..."'):
            result = provider.fetch_quote("sh.600519")
            assert result is None


class TestTencentFetchQuotesBatch:
    """fetch_quotes_batch：批量行情边界。"""

    def test_fetch_quotes_batch_empty(self) -> None:
        """空列表返回空 dict。"""
        provider = TencentProvider()
        result = provider.fetch_quotes_batch([])
        assert result == {}

    def test_fetch_quotes_batch_bj_skipped(self) -> None:
        """北交所代码在无行情数据时返回空 dict（不报错）。"""
        provider = TencentProvider()
        with patch.object(tp, "_tencent_http_get", return_value=''):
            result = provider.fetch_quotes_batch(["bj.920080"])
            # _to_tencent_symbol 对 bj 不抛异常（返回 bj920080），只是无匹配行情
            assert result == {}


class TestTencentFetchMetadata:
    """fetch_metadata：腾讯不提供元数据。"""

    def test_fetch_metadata_returns_none(self) -> None:
        """始终返回 None。"""
        provider = TencentProvider()
        assert provider.fetch_metadata("sh.600519") is None
