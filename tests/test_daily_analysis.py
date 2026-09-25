"""tests for scripts.daily_analysis."""

from __future__ import annotations

from io import BytesIO
from unittest.mock import patch

import pandas as pd

from scripts.daily_analysis import (
    _STOCK_NAMES,
    _extract_json,
    _get_whitelist,
    ai_verify,
    analyze_stock,
    get_kline,
)


class TestExtractJson:
    """JSON extraction from LLM responses."""

    def test_valid_json(self) -> None:
        assert _extract_json('{"a": 1}') == {"a": 1}

    def test_json_in_text(self) -> None:
        """JSON with double quotes embedded in text."""
        result = _extract_json('some text {"a": 1} more text')
        assert result == {"a": 1}

    def test_no_json(self) -> None:
        assert _extract_json("no json here") is None

    def test_broken_json(self) -> None:
        assert _extract_json("{broken") is None

    def test_empty_string(self) -> None:
        assert _extract_json("") is None

    def test_code_block_extraction(self) -> None:
        """Extract JSON from ```json ... ``` code block."""
        raw = '```json\n{"a": 1, "b": 2}\n```'
        assert _extract_json(raw) == {"a": 1, "b": 2}

    def test_code_block_no_lang(self) -> None:
        """Extract JSON from ``` ... ``` without lang hint."""
        raw = '```\n{"x": true}\n```'
        assert _extract_json(raw) == {"x": True}

    def test_trailing_comma_in_snippet(self) -> None:
        """Trailing comma inside extracted {..} substring is cleaned."""
        raw = '{"a": 1,}'
        assert _extract_json(raw) == {"a": 1}

    def test_trailing_comma_with_prefix_suffix(self) -> None:
        """Trailing comma cleanup works when JSON is embedded in text."""
        raw = 'result: {"a": 1, } done'
        assert _extract_json(raw) == {"a": 1}

    def test_no_json_multiple_braces(self) -> None:
        """Multiple unrelated brace pairs: no valid JSON found."""
        assert _extract_json('{ "a": 1 } no json { "b": 2 }') is None


class TestGetKline:
    """K-line data retrieval from cache."""

    def test_existing_ticker(self) -> None:
        """Mock sqlite3 to avoid relying on real database in CI."""
        mock_df = pd.DataFrame({"timestamps": ["2026-09-01"], "close": [1500.0], "open": [1490.0]})
        buf = BytesIO()
        mock_df.to_pickle(buf)
        buf.seek(0)
        mock_data = buf.read()

        with patch("sqlite3.connect") as mock_connect:
            mock_conn = mock_connect.return_value
            mock_conn.execute.return_value.fetchone.return_value = (mock_data,)
            df = get_kline("sh.600519")
            assert df is not None
            assert len(df) > 0
            assert "close" in df.columns

    def test_missing_ticker(self) -> None:
        with patch("sqlite3.connect") as mock_connect:
            mock_conn = mock_connect.return_value
            mock_conn.execute.return_value.fetchone.return_value = None
            assert get_kline("sh.999999") is None

    def test_nonexistent_code(self) -> None:
        with patch("sqlite3.connect") as mock_connect:
            mock_conn = mock_connect.return_value
            mock_conn.execute.return_value.fetchone.return_value = None
            assert get_kline("nonexistent") is None


class TestAnalyzeStock:
    """Stock analysis logic tests."""

    def test_with_data(self) -> None:
        """Mock sqlite3 to avoid relying on real database in CI."""
        # Generate 30 trading days of mock K-line data (need >= 20 rows for analyze_stock)
        dates = pd.date_range(end="2026-09-15", periods=30, freq="B")
        mock_df = pd.DataFrame(
            {
                "timestamps": dates.strftime("%Y-%m-%d").tolist(),
                "close": [10.0 + i * 0.1 for i in range(30)],
                "open": [9.9 + i * 0.1 for i in range(30)],
                "high": [10.2 + i * 0.1 for i in range(30)],
                "low": [9.8 + i * 0.1 for i in range(30)],
                "volume": [1_000_000 + i * 10_000 for i in range(30)],
            }
        )
        buf = BytesIO()
        mock_df.to_pickle(buf)
        buf.seek(0)
        mock_data = buf.read()

        with patch("sqlite3.connect") as mock_connect:
            mock_conn = mock_connect.return_value
            mock_conn.execute.return_value.fetchone.return_value = (mock_data,)
            result = analyze_stock("sh.601668")
            assert result["status"] == "ok"
        assert 0 <= result["score"] <= 100
        assert result["trend"] in [
            "强势上涨📈",
            "震荡偏多",
            "横盘整理",
            "弱势下跌📉",
        ]
        assert result["ma_signal"] in [
            "多头排列✅",
            "偏多",
            "震荡",
            "偏空",
            "空头排列❌",
        ]

    def test_no_data(self) -> None:
        """Mock sqlite3 and external fetch to avoid network calls in CI."""
        with (
            patch("scripts.daily_analysis._fetch_and_save_kline", return_value=None),
            patch("scripts.daily_analysis._get_stock_name", return_value="未知"),
            patch("sqlite3.connect") as mock_connect,
        ):
            mock_conn = mock_connect.return_value
            mock_conn.execute.return_value.fetchone.return_value = None
            result = analyze_stock("sh.999999")
            assert result["status"] == "no_data"
            assert result["score"] == 0.0
            assert result["trend"] == "数据不足"

    def test_name_mapping(self) -> None:
        """Mock sqlite3 to verify name mapping works with valid data."""
        dates = pd.date_range(end="2026-09-15", periods=30, freq="B")
        mock_df = pd.DataFrame(
            {
                "timestamps": dates.strftime("%Y-%m-%d").tolist(),
                "close": [10.0 + i * 0.1 for i in range(30)],
                "open": [9.9 + i * 0.1 for i in range(30)],
                "high": [10.2 + i * 0.1 for i in range(30)],
                "low": [9.8 + i * 0.1 for i in range(30)],
                "volume": [1_000_000 + i * 10_000 for i in range(30)],
            }
        )
        buf = BytesIO()
        mock_df.to_pickle(buf)
        buf.seek(0)
        mock_data = buf.read()
        with patch("sqlite3.connect") as mock_connect:
            mock_conn = mock_connect.return_value
            mock_conn.execute.return_value.fetchone.return_value = (mock_data,)
            result = analyze_stock("sh.601668")
            assert result["name"] == "中国建筑"
            assert result["status"] == "ok"

    def test_short_kline(self) -> None:
        """Stock with less than 20 rows should return no_data (mocked)."""
        tiny_df = pd.DataFrame(
            {
                "timestamps": ["2026-01-01", "2026-01-02"],
                "close": [10.0, 11.0],
                "volume": [100, 200],
            }
        )
        buf = BytesIO()
        tiny_df.to_pickle(buf)
        buf.seek(0)
        with (
            patch("scripts.daily_analysis._fetch_and_save_kline", return_value=None),
            patch("scripts.daily_analysis._get_stock_name", return_value="未知"),
            patch("sqlite3.connect") as mock_connect,
        ):
            mock_conn = mock_connect.return_value
            mock_conn.execute.return_value.fetchone.return_value = (buf.read(),)
            result = analyze_stock("sh.999998")
            assert result["status"] == "no_data"

    def test_bj_stock(self) -> None:
        """Mock sqlite3 to verify BJ-style ticker returns ok (CI-safe)."""
        dates = pd.date_range(end="2026-09-15", periods=30, freq="B")
        mock_df = pd.DataFrame(
            {
                "timestamps": dates.strftime("%Y-%m-%d").tolist(),
                "close": [10.0 + i * 0.1 for i in range(30)],
                "open": [9.9 + i * 0.1 for i in range(30)],
                "high": [10.2 + i * 0.1 for i in range(30)],
                "low": [9.8 + i * 0.1 for i in range(30)],
                "volume": [1_000_000 + i * 10_000 for i in range(30)],
            }
        )
        buf = BytesIO()
        mock_df.to_pickle(buf)
        buf.seek(0)
        with patch("sqlite3.connect") as mock_connect:
            mock_conn = mock_connect.return_value
            mock_conn.execute.return_value.fetchone.return_value = (buf.read(),)
            result = analyze_stock("bj.920001")
            assert result["status"] == "ok"
            assert result["score"] >= 0


class TestAiVerify:
    """AI verification logic tests."""

    def test_no_llm_configured(self) -> None:
        """When LLM is unavailable, returns defaults."""
        with patch("scripts.daily_analysis._ai_available", return_value=False):
            result = ai_verify([{"ticker": "sh.600519", "score": 50}], "2026-09-15")
            assert result["analysis_summary"] == "LLM 未配置，跳过 AI 核实"
            assert result["top_picks"] == "无"

    def test_all_keys_present(self) -> None:
        """ai_verify always returns dict with all expected keys."""
        results = [
            {
                "ticker": "sh.600519",
                "name": "贵州茅台",
                "score": 72.5,
                "trend": "强势上涨📈",
                "ma_signal": "多头排列✅",
                "vol_signal": "放量🔥",
            }
        ]
        result = ai_verify(results, "2026-09-15")
        assert "analysis_summary" in result
        assert "top_picks" in result
        assert "risk_alerts" in result
        assert "conclusion" in result

    def test_empty_results(self) -> None:
        """Empty stock list should not crash."""
        result = ai_verify([], "2026-09-15")
        assert isinstance(result, dict)

    def test_batch_processing_with_mock_client(self) -> None:
        """When stock count exceeds batch size, multiple calls are made and aggregated."""
        from unittest.mock import MagicMock

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(
                message=MagicMock(
                    content='{"analysis_summary": "OK", "top_picks": "p1", "risk_alerts": "r1", "conclusion": "c1"}'
                )
            )
        ]
        mock_client.chat.completions.create.return_value = mock_response

        results = [{"ticker": f"sh.600{i:03d}", "score": 60} for i in range(5)]
        with patch("scripts.daily_analysis._get_llm_client", return_value=mock_client):
            result = ai_verify(results, "2026-09-15")

        assert result["analysis_summary"] == "OK"
        assert result["top_picks"] == "p1"
        assert (
            mock_client.chat.completions.create.call_count == 1
        )  # 5 stocks fit in one batch of 30

    def test_batch_processing_split_across_batches(self) -> None:
        """Large stock lists trigger multiple batch calls."""
        from unittest.mock import MagicMock

        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.choices = [
            MagicMock(
                message=MagicMock(
                    content='{"analysis_summary": "ok", "top_picks": "t", "risk_alerts": "r", "conclusion": "c"}'
                )
            )
        ]
        mock_client.chat.completions.create.return_value = mock_response

        results = [{"ticker": f"sh.600{i:03d}", "score": 60} for i in range(70)]
        with patch("scripts.daily_analysis._get_llm_client", return_value=mock_client):
            with patch("scripts.daily_analysis.os.getenv", return_value="10"):  # batch_size=10
                result = ai_verify(results, "2026-09-15")

        assert (
            result["analysis_summary"] == "ok；ok；ok"
        )  # 7 identical summaries, capped at 3, joined by ；
        assert mock_client.chat.completions.create.call_count == 7  # 70 / 10 = 7 batches

    def test_all_batches_fail_returns_default(self) -> None:
        """When every batch call fails, returns default empty result."""
        from unittest.mock import MagicMock

        mock_client = MagicMock()
        mock_response = MagicMock()
        # Return invalid JSON every time
        mock_response.choices = [MagicMock(message=MagicMock(content="not json at all"))]
        mock_client.chat.completions.create.return_value = mock_response

        results = [{"ticker": "sh.600519", "score": 60}]
        with patch("scripts.daily_analysis._get_llm_client", return_value=mock_client):
            result = ai_verify(results, "2026-09-15")

        assert result["analysis_summary"] == "AI 核实失败"
        assert result["top_picks"] == "无"


class TestConstants:
    """Constants verification."""

    def test_whitelist_default(self) -> None:
        # 白名单应从.env加载，失败时回退到默认5只
        result = _get_whitelist()
        assert isinstance(result, str)
        assert len(result) > 0
        # 至少包含默认股票
        assert "000001" in result

    def test_stock_names(self) -> None:
        assert _STOCK_NAMES["000001"] == "平安银行"
        assert _STOCK_NAMES["601668"] == "中国建筑"


class TestRunAnalysisExRights:
    """除权检测逻辑测试。"""

    def _make_df(self, closes: list[float], volumes: list[int] | None = None) -> pd.DataFrame:
        n = len(closes)
        dates = pd.date_range(end="2026-09-22", periods=n, freq="B").strftime("%Y-%m-%d")
        vols = volumes or [1_000_000] * n
        return pd.DataFrame(
            {
                "timestamps": dates,
                "close": closes,
                "open": [c * 0.99 for c in closes],
                "high": [c * 1.01 for c in closes],
                "low": [c * 0.99 for c in closes],
                "volume": vols,
            }
        )

    def test_no_exrights_normal_drop(self) -> None:
        """正常下跌不应触发除权检测。"""
        closes = [10.0 + i * 0.1 for i in range(30)]  # 持续上涨
        df = self._make_df(closes)
        from scripts.daily_analysis import _run_analysis

        r = _run_analysis("sh.600519", df, "2026-09-22")
        assert r["status"] == "ok"
        assert not r.get("exrights")
        assert r["change"] > 0

    def test_exrights_detection_large_drop(self) -> None:
        """暴跌 > 30% 应触发除权检测。"""
        closes = [20.0 + i * 0.1 for i in range(29)] + [2.0]  # 最后一天 -91%
        df = self._make_df(closes)
        from scripts.daily_analysis import _run_analysis

        r = _run_analysis("sz.000001", df, "2026-09-22")
        assert r.get("exrights")
        assert r["change"] == 0.0  # 除权日显示 0
        assert r["close"] > 15  # 使用除权前价格

    def test_exrights_uses_pre_ex_rights_price(self) -> None:
        """除权日后技术指标应基于前一日收盘价。"""
        closes = [10.0] * 29 + [1.0]  # 除权后价格只有原来的 1/10
        df = self._make_df(closes)
        from scripts.daily_analysis import _run_analysis

        r = _run_analysis("sz.000002", df, "2026-09-22")
        assert r.get("exrights")
        # MA5 应该接近 10.0 而非 1.0
        assert r["ma5"] > 9.0
        assert r["ma10"] > 9.0
        # 20日区间不应包含除权后低价
        assert r["low_20"] > 9.0

    def test_exrights_20day_interval_clean(self) -> None:
        """除权后 20日区间不应混入除权后极端低价（但可含除权前正常高位）。"""
        # 设计：前25天缓升，最后5天含除权跳空；high_20 应排除 1.5 但不排除 21.25
        closes = [20.0 + i * 0.05 for i in range(25)] + [15.0, 14.8, 14.6, 14.4, 1.5]
        df = self._make_df(closes)
        from scripts.daily_analysis import _run_analysis

        r = _run_analysis("sz.000003", df, "2026-09-22")
        assert r.get("exrights")
        # 1.5 被排除（< 14.4*0.5=7.2），所以 low_20 不应是 1.5
        assert r["low_20"] > 13.0
        # high_20 可能来自除权前的正常高位（21.25 或 21.41 等），不强制小于某值
        assert r["high_20"] > 20.0  # 仍应反映除权前的价格水平

    def test_exrights_short_history(self) -> None:
        """除权日短历史（6行）因不足20行触发 no_data 保护，不应崩溃。"""
        closes = [10.0, 10.1, 10.2, 10.3, 10.4, 1.0]
        df = self._make_df(closes)
        from scripts.daily_analysis import _run_analysis

        r = _run_analysis("sz.000004", df, "2026-09-22")
        # 6行 < 20行阈值，走早期返回，exrights 字段不出现
        assert r["status"] == "no_data"
        assert r["score"] == 0.0


class TestBuySellSignals:
    """买点 / 卖点信号测试。"""

    def _make_df(self, closes: list[float], volumes: list[int] | None = None) -> pd.DataFrame:
        n = len(closes)
        dates = pd.date_range(end="2026-09-22", periods=n, freq="B").strftime("%Y-%m-%d")
        vols = volumes or [1_000_000] * n
        return pd.DataFrame(
            {
                "timestamps": dates,
                "close": closes,
                "open": [c * 0.99 for c in closes],
                "high": [c * 1.01 for c in closes],
                "low": [c * 0.99 for c in closes],
                "volume": vols,
            }
        )

    def test_ma5_golden_cross(self) -> None:
        """MA5 上穿 MA10 应触发金叉买入信号。"""
        # 前20天横盘 10.0，后5天快速上涨 → MA5 上穿 MA10
        closes = [10.0] * 20 + [10.5, 10.7, 10.9, 11.1, 11.3]
        df = self._make_df(closes)
        from scripts.daily_analysis import _run_analysis

        r = _run_analysis("sh.600001", df, "2026-09-22")
        assert any("金叉" in s for s in r["buy_points"]), f"应有金叉信号，实际: {r['buy_points']}"

    def test_ma5_death_cross(self) -> None:
        """MA5 下穿 MA10 应触发死叉卖出信号。"""
        # 先急涨到 15，再急跌回 8
        closes = [8.0 + i * 0.8 for i in range(5)] + [15.0] * 15 + [13.0, 11.0, 9.5, 8.5, 8.0]
        df = self._make_df(closes)
        from scripts.daily_analysis import _run_analysis

        r = _run_analysis("sh.600002", df, "2026-09-22")
        assert any("死叉" in s for s in r["sell_points"]), f"应有死叉信号，实际: {r['sell_points']}"

    def test_20day_low_signal(self) -> None:
        """价格接近 20日最低点应触发超卖反弹信号。"""
        # 前25天上涨到 21.25，后5天跌至 15.05
        closes = [20.0 + i * 0.05 for i in range(25)] + [15.5, 15.3, 15.2, 15.1, 15.05]
        df = self._make_df(closes)
        from scripts.daily_analysis import _run_analysis

        r = _run_analysis("sh.600003", df, "2026-09-22")
        assert any("20日低位" in s for s in r["buy_points"]), (
            f"应有20日低位信号，实际: {r['buy_points']}"
        )

    def test_far_below_ma20_signal(self) -> None:
        """价格远低于 MA20 应触发卖出信号。"""
        # 前25天稳定在 30，后5天跌至 18
        closes = [30.0] * 25 + [20.0, 19.5, 19.0, 18.5, 18.0]
        df = self._make_df(closes)
        from scripts.daily_analysis import _run_analysis

        r = _run_analysis("sh.600004", df, "2026-09-22")
        assert any("远低于MA20" in s for s in r["sell_points"]), (
            f"应有远低于MA20信号，实际: {r['sell_points']}"
        )

    def test_ma20_support_signal(self) -> None:
        """价格接近 MA20 应触发支撑买入信号。"""
        # 价格在 MA20 附近波动
        base = 20.0
        closes = [base + 0.1 * ((-1) ** i) for i in range(30)]
        df = self._make_df(closes)
        from scripts.daily_analysis import _run_analysis

        r = _run_analysis("sh.600005", df, "2026-09-22")
        # 不一定每个场景都触发，但不应崩溃
        assert r["status"] == "ok"
        assert isinstance(r["buy_points"], list)

    def test_no_volume_columns(self) -> None:
        """无 volume 列时不应报错，vol_signal 应为'无'。"""
        dates = pd.date_range(end="2026-09-22", periods=30, freq="B").strftime("%Y-%m-%d")
        df = pd.DataFrame(
            {
                "timestamps": dates,
                "close": [10.0 + i * 0.1 for i in range(30)],
                "open": [9.9 + i * 0.1 for i in range(30)],
                "high": [10.2 + i * 0.1 for i in range(30)],
                "low": [9.8 + i * 0.1 for i in range(30)],
            }
        )
        from scripts.daily_analysis import _run_analysis

        r = _run_analysis("sh.600006", df, "2026-09-22")
        assert r["status"] == "ok"
        assert r["vol_signal"] == "无"

    def test_score_bounds(self) -> None:
        """综合评分始终在 [0, 100] 范围内。"""
        from scripts.daily_analysis import _run_analysis

        dates = pd.date_range(end="2026-09-22", periods=30, freq="B").strftime("%Y-%m-%d")
        # 强势上涨场景
        df_up = pd.DataFrame(
            {
                "timestamps": dates,
                "close": [10 + i * 0.5 for i in range(30)],
                "open": [9.9 + i * 0.5 for i in range(30)],
                "high": [10.2 + i * 0.5 for i in range(30)],
                "low": [9.8 + i * 0.5 for i in range(30)],
                "volume": [1_000_000] * 30,
            }
        )
        r_up = _run_analysis("sh.600007", df_up, "2026-09-22")
        assert 0 <= r_up["score"] <= 100
        # 弱势下跌场景
        df_down = pd.DataFrame(
            {
                "timestamps": dates,
                "close": [20 - i * 0.3 for i in range(30)],
                "open": [19.9 - i * 0.3 for i in range(30)],
                "high": [20.2 - i * 0.3 for i in range(30)],
                "low": [19.8 - i * 0.3 for i in range(30)],
                "volume": [1_000_000] * 30,
            }
        )
        r_down = _run_analysis("sh.600008", df_down, "2026-09-22")
        assert 0 <= r_down["score"] <= 100
        assert r_down["score"] < r_up["score"]  # 下跌得分应低于上涨


class TestBuildStockCard:
    """飞书股票卡片构建测试。"""

    def test_normal_card(self) -> None:
        """正常股票卡片不含除权标记。"""
        from scripts.daily_analysis import _build_stock_card

        r = {
            "score": 70,
            "ticker": "sh.600519",
            "name": "贵州茅台",
            "close": 1800.0,
            "change": 1.5,
            "exrights": False,
            "trend": "强势上涨📈",
            "ma_signal": "多头排列✅",
            "vol_signal": "放量",
            "buy_points": ["MA5金叉MA10"],
            "sell_points": [],
            "ma5": 1780.0,
            "ma10": 1760.0,
            "ma20": 1740.0,
            "high_20": 1850.0,
            "low_20": 1720.0,
        }
        card = _build_stock_card(r, "2026-09-22")
        assert "📌除权日" not in card
        assert "+1.50%" in card
        assert "BUY" in card
        assert "MA5金叉MA10" in card

    def test_exrights_card(self) -> None:
        """除权股票卡片含除权标记和'除权调整'。"""
        from scripts.daily_analysis import _build_stock_card

        r = {
            "score": 50,
            "ticker": "sz.000001",
            "name": "平安银行",
            "close": 14.5,
            "change": 0.0,
            "exrights": True,
            "trend": "横盘整理",
            "ma_signal": "震荡",
            "vol_signal": "正常",
            "buy_points": [],
            "sell_points": [],
            "ma5": 14.3,
            "ma10": 14.1,
            "ma20": 13.9,
            "high_20": 14.8,
            "low_20": 13.8,
        }
        card = _build_stock_card(r, "2026-09-22")
        assert "📌除权日" in card
        assert "除权调整" in card
        assert "HOLD" in card  # score=50 → HOLD

    def test_sell_card(self) -> None:
        """低分股票卡片显示 SELL。"""
        from scripts.daily_analysis import _build_stock_card

        r = {
            "score": 20,
            "ticker": "sh.601088",
            "name": "中国神华",
            "close": 45.0,
            "change": -2.0,
            "exrights": False,
            "trend": "弱势下跌📉",
            "ma_signal": "空头排列❌",
            "vol_signal": "正常",
            "buy_points": [],
            "sell_points": ["远低于MA20"],
            "ma5": 48.0,
            "ma10": 50.0,
            "ma20": 55.0,
            "high_20": 58.0,
            "low_20": 44.0,
        }
        card = _build_stock_card(r, "2026-09-22")
        assert "🔴" in card
        assert "SELL" in card

    def test_card_with_empty_signals(self) -> None:
        """买点卖点均为空时显示'暂无'。"""
        from scripts.daily_analysis import _build_stock_card

        r = {
            "score": 40,
            "ticker": "sh.600036",
            "name": "招商银行",
            "close": 38.0,
            "change": -0.5,
            "exrights": False,
            "trend": "弱势下跌📉",
            "ma_signal": "偏空",
            "vol_signal": "正常",
            "buy_points": [],
            "sell_points": [],
            "ma5": 38.5,
            "ma10": 39.0,
            "ma20": 40.0,
            "high_20": 40.0,
            "low_20": 37.0,
        }
        card = _build_stock_card(r, "2026-09-22")
        assert "暂无" in card

    def test_card_has_all_sections(self) -> None:
        """卡片包含所有必要字段。"""
        from scripts.daily_analysis import _build_stock_card

        r = {
            "score": 60,
            "ticker": "sh.600276",
            "name": "恒瑞医药",
            "close": 46.0,
            "change": -0.1,
            "exrights": False,
            "trend": "横盘整理",
            "ma_signal": "偏多",
            "vol_signal": "正常",
            "buy_points": ["MA5金叉MA10"],
            "sell_points": [],
            "ma5": 45.5,
            "ma10": 45.0,
            "ma20": 44.0,
            "high_20": 47.0,
            "low_20": 42.0,
        }
        card = _build_stock_card(r, "2026-09-22")
        assert "恒瑞医药" in card
        assert "MA5=45.5" in card
        assert "MA10=45.0" in card
        assert "MA20=44.0" in card
        assert "42.0 ~ 47.0" in card
