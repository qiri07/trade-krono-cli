"""测试数据层和缓存。"""

from unittest.mock import MagicMock, patch

import pytest


def test_cache_creation() -> None:
    from trade_krono_cli.cache import Cache

    cache = Cache()
    assert cache._db_path.exists()
    stats = cache.stats()
    assert isinstance(stats, dict)
    assert "cache_kline_cache" in stats
    assert "cache_ta_cache" in stats
    assert "cache_kronos_cache" in stats


def test_cache_get_set_kline() -> None:
    import pandas as pd

    from trade_krono_cli.cache import Cache

    cache = Cache()
    cache.clear_all()  # 确保干净状态，避免残留数据干扰
    ticker = "sh.600519"
    start, end = "2025-01-01", "2025-01-31"
    freq = "d"

    # 空缓存
    result = cache.get_kline(ticker, start, end, freq)
    assert result is None

    # 写入
    df = pd.DataFrame(
        {
            "timestamps": pd.date_range("2025-01-01", periods=10, freq="D"),
            "open": [100.0] * 10,
            "high": [101.0] * 10,
            "low": [99.0] * 10,
            "close": [100.5] * 10,
            "volume": [1000.0] * 10,
            "amount": [100000.0] * 10,
        },
    )
    cache.set_kline(ticker, start, end, freq, df, ttl=3600)

    # 读取
    result = cache.get_kline(ticker, start, end, freq)
    assert result is not None
    assert len(result) == 10
    assert list(result.columns) == [
        "timestamps",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "amount",
    ]


def test_cache_get_set_ta() -> None:
    from trade_krono_cli.cache import Cache

    cache = Cache()
    ticker, date = "sh.600519", "2026-08-11"

    result = cache.get_ta(ticker, date)
    assert result is None

    data = {"signal": "BUY", "confidence": 78.5}
    cache.set_ta(ticker, date, data, ttl=3600)

    result = cache.get_ta(ticker, date)
    # 核心数据应匹配，缓存元数据字段单独验证
    assert result["signal"] == data["signal"]
    assert result["confidence"] == data["confidence"]
    assert "_cache_version" in result
    assert "_cache_created" in result


def test_cache_get_set_kronos() -> None:
    from trade_krono_cli.cache import Cache

    cache = Cache()
    ticker, date = "sh.600519", "2026-08-11"
    pred_len = 30

    result = cache.get_kronos(ticker, date, pred_len)
    assert result is None

    data = {"direction": "UP", "expected_change_pct": 3.2}
    cache.set_kronos(ticker, date, pred_len, data, ttl=3600)

    result = cache.get_kronos(ticker, date, pred_len)
    assert result == data


def test_cache_clear_all() -> None:
    from trade_krono_cli.cache import Cache

    cache = Cache()
    count = cache.clear_all()
    assert count >= 0
    stats = cache.stats()
    assert all(v == 0 for v in stats.values())


def test_cache_ttl_expiration() -> None:

    from trade_krono_cli.cache import Cache

    cache = Cache()
    ticker, date = "sh.600519", "2026-08-11"

    data = {"test": True}
    cache.set_ta(ticker, date, data, ttl=0)  # ttl=0 表示永久（不 expire）→ 应该能读出来
    result = cache.get_ta(ticker, date)
    # 永久缓存应始终可用，核心数据匹配
    assert result["test"] == data["test"]
    assert "_cache_version" in result


# ── cache key with config_hash / model_ver / prompt_ver ─────────────────────


class TestCacheConfigHashInvalidation:
    """配置哈希变更后缓存应失效。"""

    def test_ta_different_config_hash_miss(self) -> None:
        """不同 config_hash 应返回 None（缓存未命中）。"""
        from trade_krono_cli.cache import Cache

        cache = Cache()
        cache.clear_all()
        ticker, date = "sh.600519", "2026-08-12"
        data = {"signal": "BUY"}
        cache.set_ta(ticker, date, data, config_hash="abc123", prompt_ver="p1", model_ver="m1")
        result = cache.get_ta(ticker, date, config_hash="abc123", prompt_ver="p1", model_ver="m1")
        assert result["signal"] == data["signal"]
        assert (
            cache.get_ta(ticker, date, config_hash="abc123", prompt_ver="p1", model_ver="m1")
            == result  # 相同 key 应返回相同结果（含元数据）
        )
        assert (
            cache.get_ta(ticker, date, config_hash="different", prompt_ver="p1", model_ver="m1")
            is None
        )

    def test_ta_different_prompt_ver_miss(self) -> None:
        """不同 prompt_ver 应返回 None。"""
        from trade_krono_cli.cache import Cache

        cache = Cache()
        cache.clear_all()
        ticker, date = "sh.600519", "2026-08-12"
        data = {"signal": "BUY"}
        cache.set_ta(ticker, date, data, config_hash="h", prompt_ver="v1", model_ver="m")
        result = cache.get_ta(ticker, date, config_hash="h", prompt_ver="v1", model_ver="m")
        assert result is not None
        assert result["signal"] == data["signal"]
        assert cache.get_ta(ticker, date, config_hash="h", prompt_ver="v2", model_ver="m") is None

    def test_kronos_different_model_ver_miss(self) -> None:
        """不同 model_ver 应返回 None。"""
        from trade_krono_cli.cache import Cache

        cache = Cache()
        cache.clear_all()
        ticker, date = "sh.600519", "2026-08-12"
        pred_len = 30
        data = {"direction": "UP"}
        cache.set_kronos(ticker, date, pred_len, data, config_hash="h", model_ver="v1")
        assert cache.get_kronos(ticker, date, pred_len, config_hash="h", model_ver="v1") == data
        assert cache.get_kronos(ticker, date, pred_len, config_hash="h", model_ver="v2") is None

    def test_kronos_different_config_hash_miss(self) -> None:
        """Kronos 不同 config_hash 应返回 None。"""
        from trade_krono_cli.cache import Cache

        cache = Cache()
        cache.clear_all()
        ticker, date = "sh.600519", "2026-08-12"
        data = {"direction": "UP"}
        cache.set_kronos(ticker, date, 30, data, config_hash="hash_a", model_ver="m1")
        assert cache.get_kronos(ticker, date, 30, config_hash="hash_a", model_ver="m1") == data
        assert cache.get_kronos(ticker, date, 30, config_hash="hash_b", model_ver="m1") is None


# ── warm_history ────────────────────────────────────────────────────────────


class TestWarmHistory:
    """Cache.warm_history 测试。"""

    def test_warm_history_splits_history_and_recent(self) -> None:
        """预热时全部写入永久缓存，不区分历史/近期段。"""
        from trade_krono_cli.cache import _KLINE_HISTORICAL_TTL, Cache

        cache = Cache()
        cache.clear_all()
        ticker = "sh.600519"

        from datetime import datetime, timedelta

        import pandas as pd

        end_date = "2026-08-12"
        end_dt = datetime.strptime(end_date, "%Y-%m-%d")
        lookback_days = 730
        start_dt = end_dt - timedelta(days=lookback_days)

        rows = []
        current = start_dt
        while current <= end_dt:
            rows.append(
                {
                    "timestamps": current,
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 100.5,
                    "volume": 1e6,
                    "amount": 1e8,
                },
            )
            current += timedelta(days=1)
        df = pd.DataFrame(rows)

        with patch("trade_krono_cli.data.fetch_kline", return_value=df):
            fetched, cached = cache.warm_history(ticker, end_date, lookback_days=lookback_days)

        assert fetched == len(df)
        assert cached == 1  # 全部永久缓存，单一段

        # 全部段应为永久（ttl=0）
        with cache._conn as conn:
            rows_hist = conn.execute(
                "SELECT ttl FROM kline_cache WHERE ticker=?",
                (ticker,),
            ).fetchall()
            ttls = [r[0] for r in rows_hist]
            assert _KLINE_HISTORICAL_TTL in ttls
            assert all(t == _KLINE_HISTORICAL_TTL for t in ttls)  # 无 1h TTL 段

    def test_warm_history_empty_returns_zero(self) -> None:
        """无数据时应返回 (0, 0)。"""
        from trade_krono_cli.cache import Cache

        cache = Cache()
        cache.clear_all()
        with patch("trade_krono_cli.data.fetch_kline", return_value=None):
            fetched, cached = cache.warm_history("sh.600519", "2026-08-12")
        assert fetched == 0
        assert cached == 0


# ── validate_data_freshness ──────────────────────────────────────────────────


class TestValidateDataFreshness:
    """数据新鲜度校验测试。"""

    def _make_df(self, dates):
        """创建最小化 K 线 DataFrame。"""
        import pandas as pd

        return pd.DataFrame(
            {
                "timestamps": pd.to_datetime(dates),
                "open": [100.0] * len(dates),
                "high": [101.0] * len(dates),
                "low": [99.0] * len(dates),
                "close": [100.0] * len(dates),
                "volume": [1e6] * len(dates),
                "amount": [1e8] * len(dates),
            },
        )

    def test_fresh_data_passes(self) -> None:
        """数据最后一天是评估日期当天 → 通过。"""
        from trade_krono_cli.data import validate_data_freshness

        df = self._make_df(["2026-08-10", "2026-08-11"])
        # 不应抛异常
        validate_data_freshness(df, "2026-08-11", "sh.600519")

    def test_one_day_gap_passes(self) -> None:
        """数据最后一天是评估日期前一天 → 通过。"""
        from trade_krono_cli.data import validate_data_freshness

        df = self._make_df(["2026-08-08", "2026-08-11"])  # 周五→周一，间隔1天
        validate_data_freshness(df, "2026-08-11", "sh.600519")

    def test_suspension_raises(self) -> None:
        """数据最后一天距评估日超过 10 个交易日 → 抛异常（疑似停牌）。"""
        from trade_krono_cli.data import validate_data_freshness

        df = self._make_df(["2026-06-01", "2026-06-02"])  # 早于评估日约 2 个月
        with pytest.raises(RuntimeError, match="数据过旧|疑似停牌"):
            validate_data_freshness(df, "2026-08-11", "sh.600519")

    def test_future_data_raises(self) -> None:
        """数据最后一天晚于评估日期 → 抛异常（数据未来化）。"""
        from trade_krono_cli.data import validate_data_freshness

        df = self._make_df(["2026-08-11", "2026-08-12"])  # 含未来日期
        with pytest.raises(RuntimeError, match="数据未来化"):
            validate_data_freshness(df, "2026-08-11", "sh.600519")

    def test_missing_timestamps_column_raises(self) -> None:
        """缺少 timestamps 列 → 抛异常。"""
        import pandas as pd

        from trade_krono_cli.data import validate_data_freshness

        df = pd.DataFrame({"close": [100.0]})
        with pytest.raises(RuntimeError, match="timestamps"):
            validate_data_freshness(df, "2026-08-11", "sh.600519")

    def test_custom_max_gap(self) -> None:
        """自定义 max_gap_trading_days 时阈值更严格。"""
        from trade_krono_cli.data import validate_data_freshness

        df = self._make_df(["2026-07-01", "2026-07-02"])  # 距评估日约 40 天
        # 默认 max_gap=10 会报错
        with pytest.raises(RuntimeError):
            validate_data_freshness(df, "2026-08-11", "sh.600519", max_gap_trading_days=5)


# ── fetch_realtime_quote ─────────────────────────────────────────────────────


class TestFetchRealtimeQuote:
    """腾讯行情接口解析测试（mock urllib，不发起真实网络请求）。"""

    def _mock_response(self, body: str) -> MagicMock:
        resp = MagicMock()
        resp.read.return_value = body.encode("gbk")
        resp.__enter__ = MagicMock(return_value=resp)
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    @patch("trade_krono_cli.data.urllib.request.urlopen")
    def test_normal_quote(self, mock_urlopen) -> None:
        """标准响应：所有字段均有值。"""
        from trade_krono_cli.data import fetch_realtime_quote

        # 构造一个最小合法响应：fields[3]=price, fields[38]=turnover, fields[39]=pe,
        # fields[44]=market_cap, fields[46]=pb
        fields = ["0", "sh600519", "贵州茅台", "1680.00"]
        fields += [""] * 34  # 补齐到索引 38
        fields.append("1.23")  # fields[38] = turnover
        fields.append("35.6")  # fields[39] = pe
        fields += [""] * 4  # 补齐到索引 44
        fields.append("1200.5")  # fields[44] = market_cap (亿元)
        fields += [""] * 1  # 补齐到索引 46
        fields.append("18.2")  # fields[46] = pb
        body = "v_sh600519=" + "~".join(fields) + "~"
        mock_urlopen.return_value = self._mock_response(body)

        result = fetch_realtime_quote("sh.600519")
        assert result["price"] == 1680.0
        assert result["turnover"] == 1.23
        assert result["pe"] == 35.6
        assert result["market_cap"] == 1200.5
        assert result["pb"] == 18.2

    @patch("trade_krono_cli.data.urllib.request.urlopen")
    def test_empty_fields_become_none(self, mock_urlopen) -> None:
        """字段为空字符串时对应值为 None。"""
        from trade_krono_cli.data import fetch_realtime_quote

        fields = ["0", "sh600519", "贵州茅台", ""]  # price 为空
        fields += [""] * 50
        body = "v_sh600519=" + "~".join(fields) + "~"
        mock_urlopen.return_value = self._mock_response(body)

        result = fetch_realtime_quote("sh.600519")
        assert result["price"] is None
        assert result["pe"] is None
        assert result["market_cap"] is None

    @patch("trade_krono_cli.data.urllib.request.urlopen")
    def test_invalid_float_becomes_none(self, mock_urlopen) -> None:
        """字段为非法数字字符串（如 '--'）时返回 None，不抛异常。"""
        from trade_krono_cli.data import fetch_realtime_quote

        fields = ["0", "sh600519", "贵州茅台", "--"]  # price 是占位符
        fields += [""] * 50
        body = "v_sh600519=" + "~".join(fields) + "~"
        mock_urlopen.return_value = self._mock_response(body)

        result = fetch_realtime_quote("sh.600519")
        assert result["price"] is None  # "--" 无法转为 float
        assert isinstance(result, dict)

    @patch("trade_krono_cli.data.urllib.request.urlopen")
    def test_short_response_returns_empty(self, mock_urlopen) -> None:
        """响应字段不足 45 个时返回空字典。"""
        from trade_krono_cli.data import fetch_realtime_quote

        mock_urlopen.return_value = self._mock_response("v_sh600519=short")
        result = fetch_realtime_quote("sh.600519")
        assert result == {}

    @patch("trade_krono_cli.data.urllib.request.urlopen")
    def test_network_error_returns_empty(self, mock_urlopen) -> None:
        """网络异常时返回空字典。"""
        import urllib.error

        from trade_krono_cli.data import fetch_realtime_quote

        mock_urlopen.side_effect = urllib.error.URLError("connection refused")
        result = fetch_realtime_quote("sh.600519")
        assert result == {}

    def test_safe_float_edge_cases(self) -> None:
        """_safe_float 处理各种边界值。"""
        from trade_krono_cli.data import _safe_float

        assert _safe_float("123.45") == 123.45
        assert _safe_float("") is None
        assert _safe_float(None) is None
        assert _safe_float("--") is None
        assert _safe_float("abc") is None
        assert _safe_float("inf") is None
        assert _safe_float("-inf") is None
        assert _safe_float("nan") is None
        assert _safe_float("3.14", default=0.0) == 3.14
        assert _safe_float("", default=0.0) == 0.0


# ── fetch_kline / fetch_lookback / fetch_kline_incremental ──────────────────


class TestFetchKline:
    """trade_krono_cli.data.fetch_kline 测试（mock 缓存、工厂、baostock）。"""

    def _make_kline_df(self) -> "pd.DataFrame":  # noqa: F821 -- pd 仅在方法内局部导入，注解为字符串不求值
        import pandas as pd

        return pd.DataFrame(
            {
                "timestamps": pd.date_range("2026-01-01", periods=3, freq="D"),
                "open": [100.0, 101.0, 102.0],
                "high": [101.0, 102.0, 103.0],
                "low": [99.0, 100.0, 101.0],
                "close": [100.5, 101.5, 102.5],
                "volume": [1e6, 1.1e6, 1.2e6],
                "amount": [1e8, 1.1e8, 1.2e8],
            },
        )

    def _mock_kline_data(self, source: str = "baostock") -> MagicMock:
        kd = MagicMock()
        kd.source = source
        kd.is_empty = False
        kd.to_dataframe.return_value = self._make_kline_df()
        return kd

    @patch("trade_krono_cli.data.get_cache")
    def test_fetch_kline_cache_hit(self, mock_get_cache) -> None:
        """缓存命中时直接返回缓存数据，不走工厂。"""
        from trade_krono_cli.data import fetch_kline

        mock_cache = MagicMock()
        mock_cache.get_kline.return_value = self._make_kline_df()
        mock_get_cache.return_value = mock_cache

        result = fetch_kline("sh.600519", "2026-01-01", "2026-01-03")
        assert result is not None
        assert len(result) == 3
        mock_cache.get_kline.assert_called_once()
        mock_cache.set_kline.assert_not_called()

    @patch("trade_krono_cli.data.get_cache")
    @patch("trade_krono_cli.data.get_data_factory")
    def test_fetch_kline_factory_success(self, mock_get_factory, mock_get_cache) -> None:
        """工厂路径成功：返回 DataFrame 并写入缓存。"""
        from trade_krono_cli.data import fetch_kline

        mock_cache = MagicMock()
        mock_cache.get_kline.return_value = None
        mock_get_cache.return_value = mock_cache

        mock_factory = MagicMock()
        mock_factory.fetch_kline.return_value = self._mock_kline_data("akshare")
        mock_get_factory.return_value = mock_factory

        result = fetch_kline("sh.600519", "2026-01-01", "2026-01-03")
        assert result is not None
        assert len(result) == 3
        mock_cache.set_kline.assert_called_once()

    @patch("trade_krono_cli.data.get_cache")
    @patch("trade_krono_cli.data.get_data_factory")
    def test_fetch_kline_factory_returns_none(self, mock_get_factory, mock_get_cache) -> None:
        """工厂返回 None：回退 baostock 路径。"""
        from trade_krono_cli.data import fetch_kline

        mock_cache = MagicMock()
        mock_cache.get_kline.return_value = None
        mock_get_cache.return_value = mock_cache

        mock_factory = MagicMock()
        mock_factory.fetch_kline.return_value = None
        mock_get_factory.return_value = mock_factory

        with patch(
            "trade_krono_cli.data_providers.baostock_provider.BaostockProvider",
        ) as mock_bsp_cls:
            mock_bsp = MagicMock()
            mock_bsp.fetch_kline.return_value = None
            mock_bsp_cls.return_value = mock_bsp

            result = fetch_kline("sh.600519", "2026-01-01", "2026-01-03")
            assert isinstance(result, __import__("pandas").DataFrame)
            assert result.empty

    @patch("trade_krono_cli.data.get_cache")
    @patch("trade_krono_cli.data.get_data_factory")
    def test_fetch_kline_factory_exception(self, mock_get_factory, mock_get_cache) -> None:
        """工厂抛异常：回退 baostock 路径。"""
        from trade_krono_cli.data import fetch_kline

        mock_cache = MagicMock()
        mock_cache.get_kline.return_value = None
        mock_get_cache.return_value = mock_cache

        mock_factory = MagicMock()
        mock_factory.fetch_kline.side_effect = RuntimeError("factory down")
        mock_get_factory.return_value = mock_factory

        with patch(
            "trade_krono_cli.data_providers.baostock_provider.BaostockProvider",
        ) as mock_bsp_cls:
            mock_bsp = MagicMock()
            mock_bsp.fetch_kline.return_value = None
            mock_bsp_cls.return_value = mock_bsp

            result = fetch_kline("sh.600519", "2026-01-01", "2026-01-03")
            assert result.empty

    @patch("trade_krono_cli.data.get_cache")
    @patch("trade_krono_cli.data.get_data_factory")
    def test_fetch_kline_baostock_success(
        self, mock_get_factory, mock_get_cache
    ) -> None:
        """baostock 路径成功：返回 DataFrame。"""
        from trade_krono_cli.data import fetch_kline

        mock_cache = MagicMock()
        mock_cache.get_kline.return_value = None
        mock_get_cache.return_value = mock_cache

        mock_factory = MagicMock()
        mock_factory.fetch_kline.return_value = None
        mock_get_factory.return_value = mock_factory

        mock_bsp = MagicMock()
        mock_bsp.fetch_kline.return_value = self._mock_kline_data("baostock")
        with patch(
            "trade_krono_cli.data_providers.baostock_provider.BaostockProvider",
            return_value=mock_bsp,
        ):
            result = fetch_kline("sh.600519", "2026-01-01", "2026-01-03")
            assert result is not None
            assert len(result) == 3

    @patch("trade_krono_cli.data.get_cache")
    @patch("trade_krono_cli.data.get_data_factory")
    def test_fetch_kline_baostock_returns_none(
        self, mock_get_factory, mock_get_cache
    ) -> None:
        """baostock 返回 None：返回空 DataFrame。"""
        from trade_krono_cli.data import fetch_kline

        mock_cache = MagicMock()
        mock_cache.get_kline.return_value = None
        mock_get_cache.return_value = mock_cache

        mock_factory = MagicMock()
        mock_factory.fetch_kline.return_value = None
        mock_get_factory.return_value = mock_factory

        mock_bsp = MagicMock()
        mock_bsp.fetch_kline.return_value = None
        with patch(
            "trade_krono_cli.data_providers.baostock_provider.BaostockProvider",
            return_value=mock_bsp,
        ):
            result = fetch_kline("sh.600519", "2026-01-01", "2026-01-03")
            assert result.empty

    @patch("trade_krono_cli.data.get_cache")
    @patch("trade_krono_cli.data.get_data_factory")
    def test_fetch_kline_use_cache_false(
        self, mock_get_factory, mock_get_cache
    ) -> None:
        """use_cache=False 跳过缓存读取。"""
        from trade_krono_cli.data import fetch_kline

        mock_cache = MagicMock()
        mock_get_cache.return_value = mock_cache

        mock_factory = MagicMock()
        mock_factory.fetch_kline.return_value = self._mock_kline_data("factory")
        mock_get_factory.return_value = mock_factory

        fetch_kline("sh.600519", "2026-01-01", "2026-01-03", use_cache=False)
        mock_cache.get_kline.assert_not_called()


class TestFetchLookback:
    """trade_krono_cli.data.fetch_lookback 测试。"""

    @patch("trade_krono_cli.data.validate_data_freshness")
    @patch("trade_krono_cli.data.fetch_kline_incremental")
    def test_fetch_lookback_daily(
        self, mock_incremental, mock_validate
    ) -> None:
        """日频：start_date = end_date - lookback*2 - buffer。"""
        from trade_krono_cli.data import fetch_lookback

        mock_incremental.return_value = self._make_enough_df(500)

        fetch_lookback("sh.600519", "2026-08-11", lookback=400, frequency="d")
        call_args = mock_incremental.call_args
        assert call_args is not None
        # 日频：lookback * 2 + buffer = 400*2 + 60 = 860 天前 → 2024-04-03
        assert call_args.args[1] == "2024-04-03"

    @patch("trade_krono_cli.data.validate_data_freshness")
    @patch("trade_krono_cli.data.fetch_kline_incremental")
    def test_fetch_lookback_intraday(
        self, mock_incremental, mock_validate
    ) -> None:
        """非日频：start_date = end_date - lookback//48 - 10。"""
        from trade_krono_cli.data import fetch_lookback

        mock_incremental.return_value = self._make_enough_df(500)

        fetch_lookback("sh.600519", "2026-08-11", lookback=400, frequency="5min")
        call_args = mock_incremental.call_args
        # 5min：lookback // 48 + 10 = 400 // 48 + 10 = 8 + 10 = 18 天前
        assert call_args.args[1] == "2026-07-24"

    @patch("trade_krono_cli.data.fetch_kline_incremental")
    def test_fetch_lookback_insufficient_data(self, mock_incremental) -> None:
        """数据不足 lookback 时抛 RuntimeError。"""
        import pandas as pd

        from trade_krono_cli.data import fetch_lookback

        mock_incremental.return_value = pd.DataFrame(
            {
                "timestamps": pd.date_range("2026-01-01", periods=5, freq="D"),
                "open": [100.0] * 5,
                "high": [101.0] * 5,
                "low": [99.0] * 5,
                "close": [100.0] * 5,
                "volume": [1e6] * 5,
                "amount": [1e8] * 5,
            },
        )

        with pytest.raises(RuntimeError, match="数据不足"):
            fetch_lookback("sh.600519", "2026-08-11", lookback=400)

    def _make_enough_df(self, n: int) -> "pd.DataFrame":  # noqa: F821 -- pd 仅在方法内局部导入，注解为字符串不求值
        import pandas as pd

        return pd.DataFrame(
            {
                "timestamps": pd.date_range("2026-01-01", periods=n, freq="D"),
                "open": [100.0] * n,
                "high": [101.0] * n,
                "low": [99.0] * n,
                "close": [100.0] * n,
                "volume": [1e6] * n,
                "amount": [1e8] * n,
            },
        )


class TestFetchKlineIncremental:
    """trade_krono_cli.data.fetch_kline_incremental 测试。"""

    def _make_df(self, n: int, start: str = "2026-01-01") -> "pd.DataFrame":  # noqa: F821 -- pd 仅在方法内局部导入，注解为字符串不求值
        import pandas as pd

        return pd.DataFrame(
            {
                "timestamps": pd.date_range(start, periods=n, freq="D"),
                "open": [100.0] * n,
                "high": [101.0] * n,
                "low": [99.0] * n,
                "close": [100.0] * n,
                "volume": [1e6] * n,
                "amount": [1e8] * n,
            },
        )

    @patch("trade_krono_cli.data.get_cache")
    @patch("trade_krono_cli.data.fetch_kline")
    def test_incremental_cache_full_coverage(
        self, mock_fetch_kline, mock_get_cache
    ) -> None:
        """缓存已完整覆盖请求范围：直接返回缓存数据。"""
        from trade_krono_cli.data import fetch_kline_incremental

        mock_cache = MagicMock()
        mock_cache.get_cached_date_range.return_value = ("2026-01-01", "2026-08-13")
        mock_get_cache.return_value = mock_cache

        mock_fetch_kline.return_value = self._make_df(3, "2026-01-01")

        result = fetch_kline_incremental("sh.600519", "2026-01-05", "2026-01-07")
        assert result is not None
        assert len(result) == 3
        # 缓存全覆盖时，fetch_kline 应被调用（use_cache=True 会再次查缓存）
        mock_fetch_kline.assert_called_once()

    @patch("trade_krono_cli.data.get_cache")
    @patch("trade_krono_cli.data.fetch_kline")
    def test_incremental_no_cache_full_fetch(
        self, mock_fetch_kline, mock_get_cache
    ) -> None:
        """无缓存：全量拉取。"""
        from trade_krono_cli.data import fetch_kline_incremental

        mock_cache = MagicMock()
        mock_cache.get_cached_date_range.return_value = None
        mock_get_cache.return_value = mock_cache

        mock_fetch_kline.return_value = self._make_df(5, "2026-01-01")

        result = fetch_kline_incremental("sh.600519", "2026-01-01", "2026-01-05")
        assert result is not None
        assert len(result) == 5
        mock_fetch_kline.assert_called_once()

    @patch("trade_krono_cli.data.get_cache")
    @patch("trade_krono_cli.data.fetch_kline")
    def test_incremental_cache_expired_refetch_all(
        self, mock_fetch_kline, mock_get_cache
    ) -> None:
        """缓存已过期（cached_end < start_date）：从 start_date 重新拉取。"""
        from trade_krono_cli.data import fetch_kline_incremental

        mock_cache = MagicMock()
        mock_cache.get_cached_date_range.return_value = ("2026-01-01", "2026-01-03")
        mock_get_cache.return_value = mock_cache

        mock_fetch_kline.side_effect = [
            self._make_df(5, "2026-01-01"),  # new_df (fetch_start=start_date)
            self._make_df(3, "2026-01-01"),  # old_df
        ]

        result = fetch_kline_incremental("sh.600519", "2026-01-05", "2026-01-09")
        assert result is not None
        # cached_end=2026-01-03 < start_date=2026-01-05，fetch_start=start_date
        # start_date 作为第 2 个位置参数传入 fetch_kline
        first_call_args = mock_fetch_kline.call_args_list[0]
        assert first_call_args.args[1] == "2026-01-05"

    @patch("trade_krono_cli.data.get_cache")
    @patch("trade_krono_cli.data.fetch_kline")
    def test_incremental_cache_beyond_end(
        self, mock_fetch_kline, mock_get_cache
    ) -> None:
        """缓存已超出请求范围（cached_end >= end_date）：仅返回请求区间。"""
        from trade_krono_cli.data import fetch_kline_incremental

        mock_cache = MagicMock()
        mock_cache.get_cached_date_range.return_value = ("2026-01-01", "2026-01-10")
        mock_get_cache.return_value = mock_cache

        mock_fetch_kline.return_value = self._make_df(3, "2026-01-05")

        result = fetch_kline_incremental("sh.600519", "2026-01-05", "2026-01-07")
        assert result is not None
        assert len(result) == 3


class TestMergeKlineDfs:
    """trade_krono_cli.data._merge_kline_dfs 测试。"""

    def _make_df(self, n: int, start: str = "2026-01-01") -> "pd.DataFrame":  # noqa: F821 -- pd 仅在方法内局部导入，注解为字符串不求值
        import pandas as pd

        return pd.DataFrame(
            {
                "timestamps": pd.date_range(start, periods=n, freq="D"),
                "open": [100.0] * n,
                "high": [101.0] * n,
                "low": [99.0] * n,
                "close": [100.0] * n,
                "volume": [1e6] * n,
                "amount": [1e8] * n,
            },
        )

    def test_merge_old_none(self) -> None:
        from trade_krono_cli.data import _merge_kline_dfs

        new_df = self._make_df(3)
        result = _merge_kline_dfs(None, new_df)
        assert len(result) == 3

    def test_merge_new_none(self) -> None:
        from trade_krono_cli.data import _merge_kline_dfs

        old_df = self._make_df(3)
        result = _merge_kline_dfs(old_df, None)
        assert len(result) == 3

    def test_merge_both_none(self) -> None:
        from trade_krono_cli.data import _merge_kline_dfs

        result = _merge_kline_dfs(None, None)
        assert result.empty

    def test_merge_normal(self) -> None:
        from trade_krono_cli.data import _merge_kline_dfs

        old_df = self._make_df(5, "2026-01-01")
        new_df = self._make_df(3, "2026-01-04")
        result = _merge_kline_dfs(old_df, new_df)
        # 旧 5 行 + 新 3 行，其中 2026-01-04~06 重叠，新数据优先
        # 旧数据中早于新数据最大时间 (2026-01-06) 的保留：2026-01-01,02,03
        # 新数据：2026-01-04,05,06
        # 合并后：2026-01-01,02,03,04,05,06 → 6 行
        assert len(result) == 6


class TestWriteMergedCache:
    """trade_krono_cli.data._write_merged_cache 测试。"""

    @patch("trade_krono_cli.data.get_cache")
    def test_write_merged_cache(self, mock_get_cache) -> None:
        import pandas as pd

        from trade_krono_cli.data import _write_merged_cache

        mock_cache = MagicMock()
        mock_get_cache.return_value = mock_cache

        df = pd.DataFrame(
            {
                "timestamps": pd.date_range("2026-01-01", periods=3, freq="D"),
                "open": [100.0] * 3,
                "high": [101.0] * 3,
                "low": [99.0] * 3,
                "close": [100.0] * 3,
                "volume": [1e6] * 3,
                "amount": [1e8] * 3,
            },
        )

        _write_merged_cache(
            mock_cache,
            "sh.600519",
            df,
            "2026-01-01",
            "2026-01-03",
            "d",
            "1",
        )
        mock_cache.set_kline.assert_called_once()
