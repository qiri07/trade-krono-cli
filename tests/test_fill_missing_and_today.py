"""tests for scripts.fill_missing_and_today — 补拉计划读取与 main 参数。"""

from __future__ import annotations

import json
import time
from datetime import datetime
from types import SimpleNamespace

import scripts.fill_missing_and_today as fmat
from scripts.fill_missing_and_today import get_failed_tickers, load_sync_plan, main
from trade_krono_cli.data_providers.base import KlineData


class TestLoadSyncPlan:
    """load_sync_plan 读取 check_data_status.py 产出的补拉计划 JSON。"""

    def test_reads_reasons_failed_and_need_today(self, tmp_path) -> None:
        plan = {
            "plan": {
                "fetch_kline": {
                    "tickers": ["sh.600519", "sz.000001"],
                    "reasons": {
                        "failed": ["sh.600519"],
                        "need_today": ["sz.000001", "sh.600000"],
                    },
                }
            }
        }
        p = tmp_path / "plan.json"
        p.write_text(json.dumps(plan), encoding="utf-8")
        failed, need_today = load_sync_plan(p)
        assert failed == ["sh.600519"]
        assert need_today == ["sz.000001", "sh.600000"]

    def test_missing_file_returns_empty(self, tmp_path) -> None:
        failed, need_today = load_sync_plan(tmp_path / "nope.json")
        assert failed == []
        assert need_today == []

    def test_fallback_to_tickers_when_reasons_missing(self, tmp_path) -> None:
        plan = {"plan": {"fetch_kline": {"tickers": ["sh.600519", "sz.000001"]}}}
        p = tmp_path / "plan.json"
        p.write_text(json.dumps(plan), encoding="utf-8")
        failed, need_today = load_sync_plan(p)
        assert failed == []
        assert need_today == ["sh.600519", "sz.000001"]

    def test_corrupt_json_returns_empty(self, tmp_path) -> None:
        p = tmp_path / "plan.json"
        p.write_text("{not valid json", encoding="utf-8")
        failed, need_today = load_sync_plan(p)
        assert failed == []
        assert need_today == []


class TestMainFlags:
    """main() 的 --plan / --dry-run / --no-incremental 行为。"""

    def test_dry_run_does_not_write_cache(self, tmp_path, monkeypatch) -> None:
        """dry_run=True 时提前返回，不触发后续拉取。"""

        plan = tmp_path / "plan.json"
        plan.write_text(
            json.dumps(
                {
                    "plan": {
                        "fetch_kline": {
                            "tickers": ["sh.600519"],
                            "reasons": {
                                "failed": ["sh.600519"],
                                "need_today": ["sz.000001"],
                            },
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        called = {"factory": False}

        def _fake_factory() -> object:
            called["factory"] = True
            raise RuntimeError("should not be called in dry-run")

        monkeypatch.setattr(fmat, "get_data_factory", _fake_factory)
        monkeypatch.setattr(fmat, "fetch_missing_report", lambda: {"today": "2026-10-30"})
        monkeypatch.setattr(fmat, "print_missing_report", lambda r: None)
        main(plan_path=str(plan), dry_run=True)
        assert called["factory"] is False, "dry-run must not instantiate factory"

    def test_plan_limits_tickers(self, tmp_path) -> None:
        """--plan 时 failed_tickers 来自计划而非全库扫描。"""
        plan = tmp_path / "plan.json"
        plan.write_text(
            json.dumps(
                {
                    "plan": {
                        "fetch_kline": {
                            "reasons": {"failed": ["sh.600519"], "need_today": []}
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        failed, need_today = load_sync_plan(plan)
        assert failed == ["sh.600519"]
        assert need_today == []

    def test_main_no_plan_full_flow_smoke(self, monkeypatch) -> None:
        """完整主流程（非 dry-run、无计划）冒烟：确认 main() 走通到汇总打印。

        通过 mock 掉「全库扫描 + 网络拉取」相关函数，使流程可在测试环境无 DB /
        无网络跑通，验证 main() 的编排逻辑（补历史 + 补今日 + 汇总）无异常。
        """
        import scripts.fill_missing_and_today as fmat

        # mock 全库扫描 / 报告，避免触碰真实 DB
        monkeypatch.setattr(fmat, "get_data_factory", lambda: object())
        monkeypatch.setattr(fmat, "fetch_missing_report", lambda: {"today": "2026-10-30"})
        monkeypatch.setattr(fmat, "print_missing_report", lambda r: None)
        monkeypatch.setattr(fmat, "get_failed_tickers", lambda: [])

        # mock 今日查询 SQL（main 内联的 sqlite3.connect）与拉取函数
        import sqlite3

        # fetchall（需补今日票列表，空）与 fetchone 序列
        # fetchone 顺序：今日统计 total / tickers / latest / has_today / has_2020
        seq = iter([100, 50, "2026-10-30", 50, 30])

        class _FakeCursor:
            def execute(self, *a, **k):
                return self

            def fetchall(self):
                return []  # 无「需补今日」的票

            def fetchone(self):
                return (next(seq, 0),)

        class _FakeConn:
            def cursor(self) -> _FakeCursor:
                return _FakeCursor()

            def close(self) -> None:
                return None

        monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: _FakeConn())

        # 拉取函数：不应被调用（今日票为空 → 批次循环无任务提交）
        called = {"today": 0, "full": 0}

        def _fake_full(*a, **k):
            called["full"] += 1
            return ("baostock", 0, "")

        def _fake_today(*a, **k):
            called["today"] += 1
            return ("baostock", 0, "")

        monkeypatch.setattr(fmat, "fetch_full_range", _fake_full)
        monkeypatch.setattr(fmat, "fetch_today_incremental", _fake_today)

        # 跑完整主流程（非 dry-run）
        main(plan_path=None, dry_run=False, no_incremental=False)

        # 无失败票、无今日票 → 两个拉取函数都不应被触发
        assert called["full"] == 0
        assert called["today"] == 0

    def test_main_plan_mode_limits_tickers(self, tmp_path, monkeypatch) -> None:
        """--plan 模式下，补拉清单来自计划 JSON，不触发全库扫描。"""
        import scripts.fill_missing_and_today as fmat

        plan = tmp_path / "plan.json"
        plan.write_text(
            json.dumps(
                {
                    "plan": {
                        "fetch_kline": {
                            "reasons": {"failed": ["sh.600519"], "need_today": ["sz.000001"]}
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        scanned = {"called": False}

        def _real_get_failed() -> list[str]:
            scanned["called"] = True
            return ["sh.600519", "sh.601318", "sh.600036"]

        monkeypatch.setattr(fmat, "get_data_factory", lambda: object())
        monkeypatch.setattr(fmat, "fetch_missing_report", lambda: {"today": "2026-10-30"})
        monkeypatch.setattr(fmat, "print_missing_report", lambda r: None)
        monkeypatch.setattr(fmat, "get_failed_tickers", _real_get_failed)
        # 关键断言：有 plan 时不应调用 get_failed_tickers（全库扫描）
        main(plan_path=str(plan), dry_run=True)
        assert scanned["called"] is False, "--plan 模式不得触发全库扫描"

    def test_get_failed_tickers_uses_db(self, monkeypatch) -> None:
        """get_failed_tickers 查询 DB（默认路径，非计划）。"""
        import sqlite3

        captured = {"called": False}

        class _FakeConn:
            def connect(self, *a, **k):
                return self

            def cursor(self):
                captured["called"] = True
                fake_cur = _FakeCursor()
                return fake_cur

            def close(self):
                pass

        class _FakeCursor:
            def execute(self, *a, **k):
                return self

            def fetchall(self):
                return [("sh.600519",), ("sz.000001",)]

        monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: _FakeConn())
        result = get_failed_tickers()
        assert captured["called"]
        assert result == ["sh.600519", "sz.000001"]


def _make_kline() -> KlineData:
    """构造带有效字段数据的 KlineData（供降级链路测试使用）。"""
    return KlineData(
        timestamps=[datetime(2026, 10, 30)],
        open=[1.0],
        high=[1.1],
        low=[0.9],
        close=[1.0],
        volume=[100.0],
        amount=[100.0],
    )


class TestFetchKlineProvider:
    """_fetch_kline_provider：单 provider 拉取 + 超时 / 异常处理。"""

    def test_success_returns_data(self, monkeypatch) -> None:
        """正常拉取成功时返回 (KlineData, None, False)，不标记超时。"""

        class _OkProvider:
            def fetch_kline(
                self,
                ticker: str,
                start_date: str,
                end_date: str,
                frequency: str = "d",
                adjustflag: str = "1",
            ) -> KlineData | None:
                return _make_kline()

        factory = SimpleNamespace(get_provider=lambda name: _OkProvider())
        result, error, timed_out = fmat._fetch_kline_provider(
            factory, "sh.600519", "baostock", "2020-01-01", "2026-10-30"
        )
        assert timed_out is False
        assert error is None
        assert result is not None
        assert result.is_empty is False

    def test_timeout_returns_timed_out(self, monkeypatch) -> None:
        """fetch_kline 挂起超过 FETCH_TIMEOUT 时返回 (None, None, True)。"""

        class _HangProvider:
            def fetch_kline(
                self,
                ticker: str,
                start_date: str,
                end_date: str,
                frequency: str = "d",
                adjustflag: str = "1",
            ) -> KlineData | None:
                time.sleep(5)
                return None

        factory = SimpleNamespace(get_provider=lambda name: _HangProvider())
        monkeypatch.setattr(fmat, "FETCH_TIMEOUT", 0.05)
        result, error, timed_out = fmat._fetch_kline_provider(
            factory, "sh.600519", "baostock", "2020-01-01", "2026-10-30"
        )
        assert timed_out is True
        assert result is None
        assert error is None

    def test_exception_returns_error(self, monkeypatch) -> None:
        """fetch_kline 抛异常时返回 (None, 异常, False)，不误判为超时。"""

        class _FailProvider:
            def fetch_kline(
                self,
                ticker: str,
                start_date: str,
                end_date: str,
                frequency: str = "d",
                adjustflag: str = "1",
            ) -> KlineData | None:
                raise RuntimeError("boom")

        factory = SimpleNamespace(get_provider=lambda name: _FailProvider())
        result, error, timed_out = fmat._fetch_kline_provider(
            factory, "sh.600519", "baostock", "2020-01-01", "2026-10-30"
        )
        assert timed_out is False
        assert error is not None
        assert result is None


class TestFetchFullRangeFallback:
    """fetch_full_range：provider 级超时 / 异常后自动降级到下一个 provider。"""

    def test_falls_back_to_next_provider_on_timeout(self, monkeypatch) -> None:
        """provider 1 超时 → 自动降级尝试 provider 2，成功则返回 provider 2 结果。"""

        class _HangProvider:
            def fetch_kline(
                self,
                ticker: str,
                start_date: str,
                end_date: str,
                frequency: str = "d",
                adjustflag: str = "1",
            ) -> KlineData | None:
                time.sleep(5)
                return None

        class _OkProvider:
            def fetch_kline(
                self,
                ticker: str,
                start_date: str,
                end_date: str,
                frequency: str = "d",
                adjustflag: str = "1",
            ) -> KlineData | None:
                return _make_kline()

        factory = SimpleNamespace(
            get_provider=lambda name: _HangProvider() if name == "hang" else _OkProvider()
        )
        monkeypatch.setattr(fmat, "FETCH_TIMEOUT", 0.05)
        provider, n_rows, detail = fmat.fetch_full_range(factory, "sh.600519", ["hang", "ok"])
        assert provider == "ok"
        assert n_rows == 1

    def test_all_providers_timeout_marks_failed(self, monkeypatch) -> None:
        """所有 provider 均超时时，fetch_full_range 返回 failed。"""

        class _HangProvider:
            def fetch_kline(
                self,
                ticker: str,
                start_date: str,
                end_date: str,
                frequency: str = "d",
                adjustflag: str = "1",
            ) -> KlineData | None:
                time.sleep(5)
                return None

        factory = SimpleNamespace(get_provider=lambda name: _HangProvider())
        monkeypatch.setattr(fmat, "FETCH_TIMEOUT", 0.05)
        provider, n_rows, detail = fmat.fetch_full_range(factory, "sh.600519", ["hang"])
        assert provider == "failed"
        assert n_rows == 0
        assert "超时" in detail


class TestFetchTodayIncrementalFallback:
    """fetch_today_incremental：provider 级异常 / 超时后自动降级到下一个 provider。"""

    def test_falls_back_to_next_provider_on_exception(self, monkeypatch) -> None:
        """provider 1 抛异常 → 自动降级尝试 provider 2，成功则返回 provider 2 结果。"""

        class _FailProvider:
            def fetch_kline(
                self,
                ticker: str,
                start_date: str,
                end_date: str,
                frequency: str = "d",
                adjustflag: str = "1",
            ) -> KlineData | None:
                raise RuntimeError("boom")

        class _OkProvider:
            def fetch_kline(
                self,
                ticker: str,
                start_date: str,
                end_date: str,
                frequency: str = "d",
                adjustflag: str = "1",
            ) -> KlineData | None:
                return _make_kline()

        factory = SimpleNamespace(
            get_provider=lambda name: _FailProvider() if name == "fail" else _OkProvider()
        )
        provider, n_rows, detail = fmat.fetch_today_incremental(
            factory, "sh.600036", ["fail", "ok"]
        )
        assert provider == "ok"
        assert n_rows == 1

    def test_falls_back_to_next_provider_on_timeout(self, monkeypatch) -> None:
        """provider 1 超时 → 自动降级尝试 provider 2，成功则返回 provider 2 结果。"""

        class _HangProvider:
            def fetch_kline(
                self,
                ticker: str,
                start_date: str,
                end_date: str,
                frequency: str = "d",
                adjustflag: str = "1",
            ) -> KlineData | None:
                time.sleep(5)
                return None

        class _OkProvider:
            def fetch_kline(
                self,
                ticker: str,
                start_date: str,
                end_date: str,
                frequency: str = "d",
                adjustflag: str = "1",
            ) -> KlineData | None:
                return _make_kline()

        factory = SimpleNamespace(
            get_provider=lambda name: _HangProvider() if name == "hang" else _OkProvider()
        )
        monkeypatch.setattr(fmat, "FETCH_TIMEOUT", 0.05)
        provider, n_rows, detail = fmat.fetch_today_incremental(
            factory, "sh.600018", ["hang", "ok"]
        )
        assert provider == "ok"
        assert n_rows == 1
