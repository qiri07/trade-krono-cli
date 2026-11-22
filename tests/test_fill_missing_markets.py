"""tests for scripts.fill_missing 的 --markets 市场范围过滤。

覆盖：
- :func:`scripts._utils.market_key` 的市场归类（A股 / ETF / 北交所 / 港股）。
- ``parse_markets`` / ``_in_market_scope`` 的解析与过滤语义。
- ``main()`` 只拉取指定市场的 ticker（A股 → 港股 → ETF 分段拉取的基础）。
"""

from __future__ import annotations

import pytest

import scripts.fill_missing.cli as cli
from scripts._utils import market_key


class TestMarketKey:
    """market_key：ticker → 市场键。"""

    @pytest.mark.parametrize(
        ("ticker", "expected"),
        [
            ("sh.600519", "a"),
            ("sz.000858", "a"),
            ("sh.688798", "a"),
            ("sh.510300", "etf"),
            ("sz.159915", "etf"),
            ("sh.588000", "etf"),
            ("bj.920001", "bse"),
            ("920001", "bse"),
            ("hk.00700", "hk"),
            ("HK.00700", "hk"),
        ],
    )
    def test_classifies_ticker(self, ticker: str, expected: str) -> None:
        assert market_key(ticker) == expected


class TestParseMarkets:
    """parse_markets：--markets 取值解析。"""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("all", {"all"}),
            ("a", {"a"}),
            ("hk", {"hk"}),
            ("a,hk", {"a", "hk"}),
            (" A , HK ", {"a", "hk"}),
            ("all,a", {"all"}),
        ],
    )
    def test_accepts_valid_values(self, raw: str, expected: set[str]) -> None:
        assert set(cli.parse_markets(raw)) == expected

    def test_rejects_unknown_value(self) -> None:
        with pytest.raises(ValueError, match="不支持的"):
            cli.parse_markets("us")

    def test_empty_falls_back_to_all(self) -> None:
        assert cli.parse_markets("") == frozenset({"all"})


class TestInMarketScope:
    """_in_market_scope：市场范围过滤。"""

    def test_all_scope_keeps_everything(self) -> None:
        assert cli._in_market_scope("hk.00700", frozenset({"all"}))
        assert cli._in_market_scope("hk.00700", None)

    def test_a_scope_excludes_hk(self) -> None:
        assert cli._in_market_scope("sh.600519", frozenset({"a"}))
        assert not cli._in_market_scope("hk.00700", frozenset({"a"}))

    def test_hk_scope_excludes_a_shares(self) -> None:
        assert cli._in_market_scope("hk.00700", frozenset({"hk"}))
        assert not cli._in_market_scope("sh.600519", frozenset({"hk"}))


class TestMainMarketFilter:
    """main() 按 --markets 收窄第三步「今日增量」的拉取清单。"""

    def _run_main(self, monkeypatch, markets: frozenset[str]) -> list[str]:
        """跑一遍 main()，返回实际提交给 fetch_today_incremental 的 ticker。"""
        fetched: list[str] = []

        rows = ["sh.600519", "sz.000001", "hk.00700", "sh.510300", "bj.920001"]

        class _FakeCursor:
            def execute(self, *a, **k):
                return self

            def fetchall(self):
                return [(t,) for t in rows]

            def fetchone(self):
                return (0,)

        class _FakeConn:
            def cursor(self):
                return _FakeCursor()

            def close(self) -> None:
                return None

        monkeypatch.setattr(cli, "get_sqlite_conn", lambda *a, **k: _FakeConn())
        monkeypatch.setattr(cli, "get_data_factory", lambda: object())
        monkeypatch.setattr(cli, "print_missing_report", lambda r: None)
        monkeypatch.setattr(cli, "fetch_missing_report", lambda: {})
        monkeypatch.setattr(cli, "get_failed_tickers", lambda: [])
        monkeypatch.setattr(cli, "_load_completed_tickers", lambda: set())
        monkeypatch.setattr(cli, "_load_failed_tickers", lambda: set())
        monkeypatch.setattr(cli, "_flush_completed", lambda *a, **k: None)
        monkeypatch.setattr(cli, "_has_today_data", lambda t, d: False)
        monkeypatch.setattr(cli, "get_expected_date", lambda: "2026-11-20")
        monkeypatch.setattr(cli, "get_provider_chain", lambda t: ["fake"])
        monkeypatch.setattr(
            "scripts.backup_cache.backup_all", lambda *a, **k: {}
        )

        def _fake_today(factory, ticker, chain):
            fetched.append(ticker)
            return ("fake", 1, "ok")

        monkeypatch.setattr(cli, "fetch_today_incremental", _fake_today)
        monkeypatch.setattr(
            "scripts.fill_missing.cli.Path.write_text", lambda *a, **k: 0, raising=False
        )

        cli.main(backup=False, markets=markets)
        return fetched

    def test_a_scope_only_fetches_a_shares(self, monkeypatch) -> None:
        fetched = self._run_main(monkeypatch, frozenset({"a"}))
        assert fetched == ["sh.600519", "sz.000001"]

    def test_hk_scope_only_fetches_hk(self, monkeypatch) -> None:
        fetched = self._run_main(monkeypatch, frozenset({"hk"}))
        assert fetched == ["hk.00700"]

    def test_all_scope_fetches_a_then_hk(self, monkeypatch) -> None:
        fetched = self._run_main(monkeypatch, frozenset({"all"}))
        assert fetched == ["sh.600519", "sz.000001", "hk.00700"]
