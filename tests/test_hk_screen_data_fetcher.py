"""港股 K 线技术指标与时间窗口测试。"""

from __future__ import annotations

import pandas as pd
import pytest

from scripts.hk_screen import data_fetcher as df


def make_kline(days: int = 400, end: str = "2026-11-23") -> pd.DataFrame:
    """构造含一个远期「暴涨尖峰」的 K 线，用于验证窗口是否截断。"""
    end_ts = pd.Timestamp(end)
    dates = pd.date_range(end=end_ts, periods=days, freq="B")
    n = len(dates)
    close = [10.0 + i * 0.01 for i in range(n)]
    volume = [1_000_000.0] * n
    amount = [1.0e9] * n
    # 350 个交易日前（超出 252 日窗口）：单日 +500%，若用了全历史会污染 1Y 指标
    close[n - 350] = close[n - 351] * 6
    return pd.DataFrame(
        {
            "timestamps": dates,
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": volume,
            "amount": amount,
        }
    )


class TestTechnicalIndicators:
    def test_spike_outside_one_year_window_is_ignored(self) -> None:
        """近 1 年最大单日涨幅不应包含 350 个交易日前的尖峰。"""
        result = df._compute_technical_indicators(make_kline(), end_date="2026-11-23")

        assert result["single_day_spike_1y"] is not None
        assert result["single_day_spike_1y"] < 0.5

    def test_drawdown_uses_252_day_window(self) -> None:
        """12 个月最大回撤应基于尾部 252 日，而非全历史最高点。"""
        kline = make_kline()
        result = df._compute_technical_indicators(kline, end_date="2026-11-23")
        tail = kline.tail(252)
        expected = abs((tail["close"] - tail["close"].cummax()).div(tail["close"].cummax()).min())

        assert result["max_drawdown_12m"] == pytest.approx(expected)

    def test_drawdown_from_high_uses_full_history(self) -> None:
        """「相对历史高点跌幅」规则本身要求全历史高点。"""
        kline = make_kline()
        result = df._compute_technical_indicators(kline, end_date="2026-11-23")
        expected = (kline["close"].max() - kline["close"].iloc[-1]) / kline["close"].max()

        assert result["drawdown_from_high"] == pytest.approx(expected)

    def test_annualized_volatility_is_computed(self) -> None:
        result = df._compute_technical_indicators(make_kline(), end_date="2026-11-23")

        assert result["annualized_volatility_12m"] is not None
        assert result["annualized_volatility_12m"] >= 0

    def test_end_date_truncates_data(self) -> None:
        """指定分析日期时不能使用晚于该日期的 K 线。"""
        kline = make_kline()
        result = df._compute_technical_indicators(kline, end_date="2026-01-15")

        assert result["price"] < 20  # 早期收盘价（后段已涨到 14 以上附近）

    def test_suspension_counts_zero_volume_days(self) -> None:
        kline = make_kline()
        kline.loc[kline.tail(5).index, "volume"] = 0.0
        result = df._compute_technical_indicators(kline, end_date="2026-11-23")

        assert result["suspension_days_30d"] == 5

    def test_empty_data(self) -> None:
        assert df._compute_technical_indicators(pd.DataFrame(), end_date="2026-11-23") == {}


class TestHkConnectTriState:
    def test_failure_returns_none_not_empty_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """港股通接口失败必须返回「未知」，不能是空集合。"""
        import akshare as ak

        def _boom() -> None:
            raise ConnectionError("network down")

        monkeypatch.setattr(ak, "stock_hk_ggt_components_em", _boom)

        assert df.get_hk_connect_stocks() is None

    def test_success_returns_tickers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import akshare as ak

        monkeypatch.setattr(
            ak,
            "stock_hk_ggt_components_em",
            lambda: pd.DataFrame({"代码": ["00700", "00941"]}),
        )

        assert df.get_hk_connect_stocks() == {"hk.00700", "hk.00941"}


class TestRawDataAssembly:
    def test_metadata_priority_and_tri_state(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """元数据优先于技术指标；未提供的布尔字段保持 None。"""
        ticker = "hk.00700"
        monkeypatch.setattr(df, "_load_hk_kline_from_cache", lambda tickers: {})
        monkeypatch.setattr(
            df,
            "load_hk_metadata",
            lambda tickers: {
                ticker: {
                    "name": "腾讯控股",
                    "pe_ttm": 11.4,
                    "market_cap": 3e12,
                    "is_hkd": True,
                    "is_hk_connect": False,
                    "avg_roe_3y": 0.19,
                    "clean_audit_2y": None,
                }
            },
        )
        monkeypatch.setattr(df, "get_hk_connect_stocks", lambda: {"hk.00700"})

        raw = df.fetch_hk_raw_data(
            [ticker], hk_connect_stocks={"hk.00700"}, date="2026-11-23", live_fallback=False
        )[0]

        assert raw.pe_ttm == 11.4
        assert raw.is_hk_connect is False  # 元数据明确 False，不被集合中的 True 覆盖
        assert raw.is_hkd is True
        assert raw.clean_audit_2y is None
        assert raw.name == "腾讯控股"

    def test_turnover_rate_derived_from_cap(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """元数据缺换手率时，用近 12 月日均成交额/总市值近似（百分数）。"""
        ticker = "hk.00700"
        kline = make_kline()
        monkeypatch.setattr(df, "_load_hk_kline_from_cache", lambda tickers: {ticker: kline})
        monkeypatch.setattr(
            df,
            "load_hk_metadata",
            lambda tickers: {ticker: {"market_cap": 1.0e11, "is_hkd": True}},
        )

        raw = df.fetch_hk_raw_data(
            [ticker], hk_connect_stocks=set(), date="2026-11-23", live_fallback=False
        )[0]

        assert raw.turnover_rate_12m_avg == pytest.approx(1.0)  # 1e9 / 1e11 = 1%

    def test_raw_to_screen_result_preserves_unknowns(self) -> None:
        raw = df.HKRawData(ticker="hk.00700")

        result = df.raw_to_screen_result(raw)

        assert result.is_hkd is None
        assert result.is_common_stock is None
        assert result.suspension_days_30d is None
        assert result.rights_issues_12m is None
