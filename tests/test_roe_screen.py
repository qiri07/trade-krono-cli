"""tests for scripts.roe_screen — 白名单 ROE > 15% 筛选。"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from scripts._utils import ticker_to_thscode


class TestTickerToThscode:
    """ticker → thscode 转换测试。"""

    def test_sh_ticker(self) -> None:
        assert ticker_to_thscode("sh.600519") == "600519.SH"

    def test_sz_ticker(self) -> None:
        assert ticker_to_thscode("sz.000001") == "000001.SZ"

    def test_no_prefix(self) -> None:
        assert ticker_to_thscode("600519") == "600519"


class TestApiGet:
    """_api_get 基础网络请求测试。"""

    def test_no_api_key_returns_none(self) -> None:
        with patch.dict("os.environ", {"HITHINK_FINANCE_API_KEY": ""}, clear=True):
            import importlib

            import scripts.roe_screen as mod
            importlib.reload(mod)
            result = mod._api_get("/api/test", {"a": "1"})
            assert result is None

    def test_success_response(self) -> None:
        import scripts.roe_screen as mod
        mod._API_KEY = "fake_key"
        mock_output = '{"code":0,"data":{"name":"贵州茅台","abilities":[{"indicators":[{"index_id":"index_weighted_avg_roe","value":"28.5"}]}]}}\n200'
        with patch("scripts.roe_screen.subprocess.run") as mock_run:
            mock_run.return_value.stdout = mock_output
            result = mod._api_get("/api/test", {"a": "1"})
        assert result is not None
        assert result["code"] == 0

    def test_http_error_returns_none(self) -> None:
        import scripts.roe_screen as mod
        mod._API_KEY = "fake_key"
        with patch("scripts.roe_screen.subprocess.run") as mock_run:
            mock_run.return_value.stdout = '{"code":1}\n500'
            result = mod._api_get("/api/test", {"a": "1"})
        assert result is None

    def test_network_error_returns_none(self) -> None:
        import scripts.roe_screen as mod
        mod._API_KEY = "fake_key"
        with patch("scripts.roe_screen.subprocess.run", side_effect=RuntimeError("timeout")):
            result = mod._api_get("/api/test", {"a": "1"})
        assert result is None


class TestFetchRoe:
    """单股 ROE 获取测试。"""

    def test_success(self) -> None:
        from scripts.roe_screen import _fetch_roe

        mock_response = {
            "code": 0,
            "data": {
                "name": "贵州茅台",
                "abilities": [
                    {"indicators": [{"index_id": "index_weighted_avg_roe", "value": "28.5"}]}
                ],
            },
        }
        with patch("scripts.roe_screen._api_get", return_value=mock_response):
            result = _fetch_roe("600519.SH", "2025")
        assert result is not None
        assert result["roe"] == 28.5
        assert result["name"] == "贵州茅台"

    def test_missing_roe_index(self) -> None:
        from scripts.roe_screen import _fetch_roe

        mock_response = {
            "code": 0,
            "data": {
                "name": "某股票",
                "abilities": [
                    {"indicators": [{"index_id": "other_index", "value": "10"}]}
                ],
            },
        }
        with patch("scripts.roe_screen._api_get", return_value=mock_response):
            result = _fetch_roe("600519.SH", "2025")
        assert result is None

    def test_api_error_returns_none(self) -> None:
        from scripts.roe_screen import _fetch_roe

        with patch("scripts.roe_screen._api_get", return_value=None):
            result = _fetch_roe("600519.SH", "2025")
        assert result is None

    def test_nonzero_code_returns_none(self) -> None:
        from scripts.roe_screen import _fetch_roe

        with patch("scripts.roe_screen._api_get", return_value={"code": 1}):
            result = _fetch_roe("600519.SH", "2025")
        assert result is None


class TestBatchFetchRoe:
    """批量 ROE 获取测试。"""

    def test_partial_success(self) -> None:
        from scripts.roe_screen import _batch_fetch_roe

        # _fetch_roe returns the PARSED result, not raw API response
        success_parsed = {"roe": 28.5, "name": "贵州茅台"}
        with patch("scripts.roe_screen._fetch_roe", side_effect=[success_parsed, None]):
            results = _batch_fetch_roe(["sh.600519", "sz.000001"], "2025")
        assert len(results) == 1
        assert "sh.600519" in results
        assert results["sh.600519"]["roe"] == 28.5


class TestLoadWhitelist:
    """白名单加载测试。"""

    def test_with_prefixes(self) -> None:
        with patch.dict(
            "os.environ",
            {"SYNC_WHITELIST": "sh.600519,sz.000001,hk.00700"},
            clear=True,
        ):
            from scripts.roe_screen import _load_whitelist

            result = _load_whitelist()
            assert "sh.600519" in result
            assert "sz.000001" in result
            assert "hk.00700" in result

    def test_bare_codes_auto_prefixed(self) -> None:
        with patch.dict(
            "os.environ",
            {"SYNC_WHITELIST": "600519,000001"},
            clear=True,
        ):
            from scripts.roe_screen import _load_whitelist

            result = _load_whitelist()
            assert "sh.600519" in result
            assert "sz.000001" in result

    def test_empty_env_exits(self) -> None:
        with patch.dict("os.environ", {"SYNC_WHITELIST": ""}, clear=True):
            from scripts.roe_screen import _load_whitelist

            with pytest.raises(SystemExit):
                _load_whitelist()

    def test_missing_env_exits(self) -> None:
        env = {k: v for k, v in __import__("os").environ.items() if k != "SYNC_WHITELIST"}
        with patch.dict("os.environ", env, clear=True):
            from scripts.roe_screen import _load_whitelist

            with pytest.raises(SystemExit):
                _load_whitelist()


class TestBuildSummaryCard:
    """飞书摘要卡片构建测试。"""

    def test_empty_results(self) -> None:
        from scripts.roe_screen import _build_summary_card

        card = _build_summary_card([], "2026-10-10", {})
        assert "2026-10-10" in card
        assert "0 只" in card

    def test_with_results(self) -> None:
        from scripts.roe_screen import _build_summary_card

        results = [
            {"ticker": "sh.600519", "name": "贵州茅台", "roe": 28.5, "pe": None},
            {"ticker": "sz.000001", "name": "平安银行", "roe": 18.2, "pe": 6.5},
        ]
        card = _build_summary_card(results, "2026-10-10", {})
        assert "贵州茅台" in card
        assert "28.5%" in card
        assert "2026-10-10" in card

    def test_with_ai_result(self) -> None:
        from scripts.roe_screen import _build_summary_card

        results = [{"ticker": "sh.600519", "name": "茅台", "roe": 28.5, "pe": None}]
        ai = {"analysis_summary": "茅台基本面稳健，建议关注。"}
        card = _build_summary_card(results, "2026-10-10", ai)
        assert "AI 核实" in card
        assert "基本面稳健" in card


class TestMain:
    """main 主流程测试。"""

    def test_no_a_stocks_exits(self) -> None:
        from scripts.roe_screen import main

        with (
            patch("scripts.roe_screen._load_whitelist", return_value=["hk.00700"]),
            patch("scripts.roe_screen.logger") as mock_logger,
            patch("sys.argv", ["roe_screen.py"]),
        ):
            main()
            mock_logger.warning.assert_called()
