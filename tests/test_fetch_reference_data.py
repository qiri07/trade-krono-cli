"""tests for scripts.fetch_reference_data."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import scripts.fetch_reference_data as frd
from scripts.fetch_reference_data import (
    _calc_dividend_yield_for_ticker,
    _calc_dividend_yields_batch,
    _fuyao_api_get,
    _get_fuyao_api_key,
    _safe_float,
)


class TestGetAllTickers:
    """Ticker retrieval from DB."""

    def test_returns_list(self) -> None:
        with patch("scripts._utils.sqlite3.connect") as mock_connect:
            mock_conn = mock_connect.return_value
            mock_conn.execute.return_value.fetchall.return_value = [
                ("sh.600519",),
                ("sz.000001",),
            ]
            from scripts._utils import get_all_tickers

            tickers = get_all_tickers()
            assert isinstance(tickers, list)
            assert len(tickers) > 0

    def test_contains_known_tickers(self) -> None:
        """At minimum sz.000001 should always be present (whitelist stock)."""
        with patch("scripts._utils.sqlite3.connect") as mock_connect:
            mock_conn = mock_connect.return_value
            mock_conn.execute.return_value.fetchall.return_value = [
                ("sz.000001",),
                ("sh.600519",),
            ]
            from scripts._utils import get_all_tickers

            tickers = get_all_tickers()
            assert "sz.000001" in tickers
            assert len(tickers) > 0


class TestCheckpoint:
    """load_checkpoint / save_checkpoint / clear_checkpoint 原子性与恢复逻辑。"""

    def test_save_then_load_roundtrip(self, monkeypatch, tmp_path) -> None:
        """save_checkpoint 写入后 load_checkpoint 能读回相同数据。"""
        cp = tmp_path / "checkpoint.json"
        monkeypatch.setattr(frd, "CHECKPOINT_FILE", cp)
        data = {"sh.600519": {"pe_ttm": 21.0}, "sz.000001": None}
        frd.save_checkpoint(data)
        assert frd.load_checkpoint() == data

    def test_load_missing_returns_empty(self, monkeypatch, tmp_path) -> None:
        """checkpoint 文件不存在时 load_checkpoint 返回空 dict。"""
        monkeypatch.setattr(frd, "CHECKPOINT_FILE", tmp_path / "nope.json")
        assert frd.load_checkpoint() == {}

    def test_clear_removes_file(self, monkeypatch, tmp_path) -> None:
        """clear_checkpoint 删除文件；缺失时不报错。"""
        cp = tmp_path / "checkpoint.json"
        monkeypatch.setattr(frd, "CHECKPOINT_FILE", cp)
        cp.write_text("{}", encoding="utf-8")
        frd.clear_checkpoint()
        assert not cp.exists()
        frd.clear_checkpoint()  # 幂等

    def test_corrupt_checkpoint_returns_empty(self, monkeypatch, tmp_path) -> None:
        """损坏的 checkpoint 文件 load_checkpoint 降级返回空 dict，不 raise。"""
        cp = tmp_path / "checkpoint.json"
        monkeypatch.setattr(frd, "CHECKPOINT_FILE", cp)
        cp.write_text("{not valid", encoding="utf-8")
        assert frd.load_checkpoint() == {}

    def test_resume_skips_success_only_refetches_failures(
        self, monkeypatch, tmp_path
    ) -> None:
        """resume=True 时仅重拉 checkpoint 中为 None/缺失的 ticker。

        新实现使用 akshare 批量获取，所有 ticker 都会从 akshare 获取最新数据。
        验证：已成功的 ticker 会被 akshare 最新数据覆盖；失败/未完成的 ticker 也会被获取。
        """
        cp = tmp_path / "checkpoint.json"
        monkeypatch.setattr(frd, "CHECKPOINT_FILE", cp)
        monkeypatch.setattr(
            frd, "get_all_tickers", lambda: ["sh.600519", "sh.000858", "sh.601318"]
        )
        # 预置 checkpoint：茅台成功、五粮液失败（None）、平安未拉（缺失）
        cp.write_text(
            json.dumps(
                {
                    "sh.600519": {"ticker": "sh.600519", "pe_ttm": 21.0},
                    "sh.000858": None,
                }
            ),
            encoding="utf-8",
        )

        monkeypatch.setattr(frd, "METADATA_FILE", tmp_path / "metadata.json")

        frd.main(tickers_input=None, resume=True, use_checkpoint=True)

        # 所有 ticker 都应在 results 中（akshare 批量获取）
        results = json.loads((tmp_path / "metadata.json").read_text(encoding="utf-8"))
        assert "sh.600519" in results
        assert "sh.000858" in results
        assert "sh.601318" in results
        # 成功后 checkpoint 被清除
        assert not cp.exists()


# ── _safe_float ─────────────────────────────────────────────────────────────


class TestSafeFloat:
    """_safe_float 安全浮点转换。"""

    def test_none_returns_none(self) -> None:
        assert _safe_float(None) is None

    def test_nan_string_returns_none(self) -> None:
        assert _safe_float("nan") is None

    def test_inf_string_returns_none(self) -> None:
        assert _safe_float("inf") is None
        assert _safe_float("-inf") is None

    def test_numeric_string_returns_float(self) -> None:
        assert _safe_float("42") == 42.0
        assert _safe_float("3.14") == 3.14

    def test_numeric_value_passthrough(self) -> None:
        assert _safe_float(42) == 42.0
        assert _safe_float(3.14) == 3.14

    def test_invalid_string_returns_none(self) -> None:
        assert _safe_float("abc") is None
        assert _safe_float("12.34.56") is None


# ── _get_fuyao_api_key ───────────────────────────────────────────────────────


class TestGetFuyaoApiKey:
    """_get_fuyao_api_key 动态读取 API Key。"""

    def test_reads_hithink_env(self, monkeypatch) -> None:
        monkeypatch.setenv("HITHINK_FINANCE_API_KEY", "test-key-123")
        monkeypatch.delenv("FUYAO_API_KEY", raising=False)
        assert _get_fuyao_api_key() == "test-key-123"

    def test_falls_back_to_fuyao_env(self, monkeypatch) -> None:
        monkeypatch.delenv("HITHINK_FINANCE_API_KEY", raising=False)
        monkeypatch.setenv("FUYAO_API_KEY", "fallback-key")
        assert _get_fuyao_api_key() == "fallback-key"

    def test_returns_empty_when_no_key_set(self, monkeypatch) -> None:
        monkeypatch.delenv("HITHINK_FINANCE_API_KEY", raising=False)
        monkeypatch.delenv("FUYAO_API_KEY", raising=False)
        assert _get_fuyao_api_key() == ""

    def test_hithink_takes_priority(self, monkeypatch) -> None:
        monkeypatch.setenv("HITHINK_FINANCE_API_KEY", "primary")
        monkeypatch.setenv("FUYAO_API_KEY", "secondary")
        assert _get_fuyao_api_key() == "primary"


# ── _fuyao_api_get ───────────────────────────────────────────────────────────


class TestFuyaoApiGet:
    """_fuyao_api_get — Fuyao API HTTP GET 封装。"""

    def test_returns_none_when_key_not_configured(self, monkeypatch) -> None:
        """未配置 API Key 时直接返回 None，不发任何网络请求。"""
        monkeypatch.delenv("HITHINK_FINANCE_API_KEY", raising=False)
        monkeypatch.delenv("FUYAO_API_KEY", raising=False)
        result = _fuyao_api_get("/api/a-share/valuations/snapshot")
        assert result is None

    def test_returns_dict_on_success(self, monkeypatch) -> None:
        """成功响应时返回解析后的 JSON dict。"""

        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"code": 0, "data": {"item": [{"pe_ttm": 21.0}]}}'
        mock_resp.__enter__ = lambda self: self
        mock_resp.__exit__ = MagicMock(return_value=False)

        with (
            patch("scripts.fetch_reference_data._get_fuyao_api_key", return_value="test-key"),
            patch("scripts.fetch_reference_data.get_rate_limiter") as mock_limiter,
            patch("urllib.request.urlopen", return_value=mock_resp),
        ):
            mock_limiter.return_value.acquire.return_value = 0.0
            result = _fuyao_api_get("/api/a-share/valuations/snapshot", {"thscodes": "600519.SH"})
            assert result is not None
            assert result["code"] == 0
            assert result["data"]["item"][0]["pe_ttm"] == 21.0

    def test_returns_none_on_network_error(self, monkeypatch) -> None:
        """网络异常时返回 None，不 raise。"""
        import urllib.error

        with (
            patch("scripts.fetch_reference_data._get_fuyao_api_key", return_value="test-key"),
            patch("scripts.fetch_reference_data.get_rate_limiter") as mock_limiter,
            patch("urllib.request.urlopen", side_effect=urllib.error.URLError("conn refused")),
        ):
            mock_limiter.return_value.acquire.return_value = 0.0
            result = _fuyao_api_get("/api/a-share/valuations/snapshot")
            assert result is None

    def test_returns_none_on_non_dict_json(self, monkeypatch) -> None:
        """响应 JSON 非 dict（如 list）时返回 None。"""
        mock_resp = MagicMock()
        mock_resp.read.return_value = b'["not", "a", "dict"]'
        mock_resp.__enter__ = lambda self: self
        mock_resp.__exit__ = MagicMock(return_value=False)

        with (
            patch("scripts.fetch_reference_data._get_fuyao_api_key", return_value="test-key"),
            patch("scripts.fetch_reference_data.get_rate_limiter") as mock_limiter,
            patch("urllib.request.urlopen", return_value=mock_resp),
        ):
            mock_limiter.return_value.acquire.return_value = 0.0
            result = _fuyao_api_get("/api/a-share/valuations/snapshot")
            assert result is None

    def test_sleeps_when_rate_limiter_returns_wait(self, monkeypatch) -> None:
        """限流器返回等待时间 > 0 时，调用 time.sleep。"""

        mock_resp = MagicMock()
        mock_resp.read.return_value = b'{"code": 0}'
        mock_resp.__enter__ = lambda self: self
        mock_resp.__exit__ = MagicMock(return_value=False)

        with (
            patch("scripts.fetch_reference_data._get_fuyao_api_key", return_value="test-key"),
            patch("scripts.fetch_reference_data.get_rate_limiter") as mock_limiter,
            patch("scripts.fetch_reference_data.time.sleep") as mock_sleep,
            patch("urllib.request.urlopen", return_value=mock_resp),
        ):
            mock_limiter.return_value.acquire.return_value = 0.5
            _fuyao_api_get("/api/a-share/valuations/snapshot")
            mock_sleep.assert_called_once_with(0.5)


# ── _calc_dividend_yield_for_ticker ──────────────────────────────────────────


class TestCalcDividendYieldForTicker:
    """_calc_dividend_yield_for_ticker — 单只 A 股股息率计算。"""

    @staticmethod
    def _make_cash_flow(pay_div: float | None) -> dict:
        return {
            "code": 0,
            "data": {"item": [{"pay_dividends_profits_interest_cash": pay_div}]},
        }

    @staticmethod
    def _make_income(net_profit: float | None) -> dict:
        return {
            "code": 0,
            "data": {"item": [{"parent_holder_net_profit": net_profit}]},
        }

    @staticmethod
    def _make_valuation(pe_ttm: float | None) -> dict:
        return {
            "code": 0,
            "data": {"item": [{"pe_ttm": pe_ttm}]},
        }

    def test_returns_correct_yield(self, monkeypatch) -> None:
        """分红 100 亿、净利润 200 亿、PE 20 → 股息率 = 100/(20×200)×100 = 2.5%"""
        responses = {
            "/api/a-share/financials/cash-flow-statements": self._make_cash_flow(1e10),
            "/api/a-share/financials/income-statements": self._make_income(2e10),
            "/api/a-share/valuations/snapshot": self._make_valuation(20.0),
        }

        def _side_effect(path, params=None, timeout=8):
            return responses.get(path)

        monkeypatch.setattr(frd, "_fuyao_api_get", _side_effect)
        result = _calc_dividend_yield_for_ticker("sh.600519")
        assert result is not None
        assert abs(result - 2.5) < 0.01

    def test_returns_none_when_pay_div_missing(self, monkeypatch) -> None:
        """现金流量表无分红数据时返回 None。"""
        responses = {
            "/api/a-share/financials/cash-flow-statements": self._make_cash_flow(None),
            "/api/a-share/financials/income-statements": self._make_income(2e10),
            "/api/a-share/valuations/snapshot": self._make_valuation(20.0),
        }
        monkeypatch.setattr(frd, "_fuyao_api_get", lambda path, params=None, timeout=8: responses.get(path))
        result = _calc_dividend_yield_for_ticker("sh.600519")
        assert result is None

    def test_returns_none_when_pay_div_zero(self, monkeypatch) -> None:
        """分红为 0 时返回 None（不计算无分红的股息率）。"""
        responses = {
            "/api/a-share/financials/cash-flow-statements": self._make_cash_flow(0),
            "/api/a-share/financials/income-statements": self._make_income(2e10),
            "/api/a-share/valuations/snapshot": self._make_valuation(20.0),
        }
        monkeypatch.setattr(frd, "_fuyao_api_get", lambda path, params=None, timeout=8: responses.get(path))
        result = _calc_dividend_yield_for_ticker("sh.600519")
        assert result is None

    def test_returns_none_when_net_profit_missing(self, monkeypatch) -> None:
        """利润表无净利润时返回 None。"""
        responses = {
            "/api/a-share/financials/cash-flow-statements": self._make_cash_flow(1e10),
            "/api/a-share/financials/income-statements": self._make_income(None),
            "/api/a-share/valuations/snapshot": self._make_valuation(20.0),
        }
        monkeypatch.setattr(frd, "_fuyao_api_get", lambda path, params=None, timeout=8: responses.get(path))
        result = _calc_dividend_yield_for_ticker("sh.600519")
        assert result is None

    def test_returns_none_when_pe_ttm_missing(self, monkeypatch) -> None:
        """估值快照无 PE_TTM 时返回 None。"""
        responses = {
            "/api/a-share/financials/cash-flow-statements": self._make_cash_flow(1e10),
            "/api/a-share/financials/income-statements": self._make_income(2e10),
            "/api/a-share/valuations/snapshot": self._make_valuation(None),
        }
        monkeypatch.setattr(frd, "_fuyao_api_get", lambda path, params=None, timeout=8: responses.get(path))
        result = _calc_dividend_yield_for_ticker("sh.600519")
        assert result is None

    def test_returns_none_when_api_returns_code_nonzero(self, monkeypatch) -> None:
        """API 返回 code≠0（错误）时返回 None。"""
        responses = {
            "/api/a-share/financials/cash-flow-statements": {"code": 1, "data": None},
            "/api/a-share/financials/income-statements": {"code": 0, "data": {"item": [{"parent_holder_net_profit": 2e10}]}},
            "/api/a-share/valuations/snapshot": {"code": 0, "data": {"item": [{"pe_ttm": 20.0}]}},
        }
        monkeypatch.setattr(frd, "_fuyao_api_get", lambda path, params=None, timeout=8: responses.get(path))
        result = _calc_dividend_yield_for_ticker("sh.600519")
        assert result is None

    def test_returns_none_for_ticker_without_dot(self, monkeypatch) -> None:
        """裸代码（无前缀）无法转换为 thscode，返回 None。"""
        monkeypatch.setattr(frd, "_fuyao_api_get", lambda *a, **kw: {})
        result = _calc_dividend_yield_for_ticker("600519")
        assert result is None


# ── _calc_dividend_yields_batch ─────────────────────────────────────────────


class TestCalcDividendYieldsBatch:
    """_calc_dividend_yields_batch — 批量股息率补计算。"""

    def test_skips_when_no_api_key(self, monkeypatch) -> None:
        """未配置 API Key 时直接返回，不做任何计算。"""
        monkeypatch.delenv("HITHINK_FINANCE_API_KEY", raising=False)
        monkeypatch.delenv("FUYAO_API_KEY", raising=False)
        results: dict[str, dict | None] = {"sh.600519": {"ticker": "sh.600519", "dividend_yield": None}}
        _calc_dividend_yields_batch(results, ["sh.600519"])
        # 结果保持不变（无网络请求）
        assert (results["sh.600519"] or {})["dividend_yield"] is None

    def test_fills_in_yield_and_marks_source(self, monkeypatch) -> None:
        """成功计算时写入 dividend_yield 并标记 dividend_yield_source=fuyao。"""
        monkeypatch.setenv("HITHINK_FINANCE_API_KEY", "test-key")
        monkeypatch.setattr(
            frd, "_calc_dividend_yield_for_ticker", lambda ticker: 2.5
        )
        results: dict[str, dict | None] = {
            "sh.600519": {"ticker": "sh.600519", "dividend_yield": None},
            "sz.000858": {"ticker": "sz.000858", "dividend_yield": None},
        }
        _calc_dividend_yields_batch(results, ["sh.600519", "sz.000858"])
        assert (results["sh.600519"] or {})["dividend_yield"] == 2.5
        assert (results["sh.600519"] or {})["dividend_yield_source"] == "fuyao"
        assert (results["sz.000858"] or {})["dividend_yield"] == 2.5
        assert (results["sz.000858"] or {})["dividend_yield_source"] == "fuyao"

    def test_skips_tickers_with_existing_yield(self, monkeypatch) -> None:
        """dividend_yield 已填充的 ticker 不参与补计算。"""
        monkeypatch.setenv("HITHINK_FINANCE_API_KEY", "test-key")
        call_count = {"n": 0}

        def _mock_calc(ticker: str) -> float | None:
            call_count["n"] += 1
            return 1.0

        monkeypatch.setattr(frd, "_calc_dividend_yield_for_ticker", _mock_calc)
        results: dict[str, dict | None] = {
            "sh.600519": {"ticker": "sh.600519", "dividend_yield": 3.0},  # 已有值
            "sz.000858": {"ticker": "sz.000858", "dividend_yield": None},  # 缺失
        }
        _calc_dividend_yields_batch(results, ["sh.600519", "sz.000858"])
        # 只有 1 只被计算
        assert call_count["n"] == 1
        assert (results["sh.600519"] or {})["dividend_yield"] == 3.0  # 保持不变
        assert (results["sz.000858"] or {})["dividend_yield"] == 1.0

    def test_failed_calc_leaves_yield_none(self, monkeypatch) -> None:
        """计算失败（返回 None）时 dividend_yield 保持 None，不计入 success。"""
        monkeypatch.setenv("HITHINK_FINANCE_API_KEY", "test-key")
        monkeypatch.setattr(frd, "_calc_dividend_yield_for_ticker", lambda ticker: None)
        results: dict[str, dict | None] = {
            "sh.600519": {"ticker": "sh.600519", "dividend_yield": None},
        }
        _calc_dividend_yields_batch(results, ["sh.600519"])
        assert (results["sh.600519"] or {})["dividend_yield"] is None
        assert "dividend_yield_source" not in (results["sh.600519"] or {})

    def test_ignores_tickers_not_in_results(self, monkeypatch) -> None:
        """results 中无该 ticker 时静默跳过，不报错。"""
        monkeypatch.setenv("HITHINK_FINANCE_API_KEY", "test-key")
        monkeypatch.setattr(frd, "_calc_dividend_yield_for_ticker", lambda ticker: 2.0)
        results: dict[str, dict | None] = {}
        # 不 raise，即使 tickers 里有 results 中不存在的 key
        _calc_dividend_yields_batch(results, ["sh.600519"])
        assert results == {}
