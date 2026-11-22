"""tests for scripts.fill_missing_and_today — 补拉计划读取与 main 参数。"""

from __future__ import annotations

import json
import time
import warnings
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
                {"plan": {"fetch_kline": {"reasons": {"failed": ["sh.600519"], "need_today": []}}}}
            ),
            encoding="utf-8",
        )
        failed, need_today = load_sync_plan(plan)
        assert failed == ["sh.600519"]
        assert need_today == []

    def test_main_no_plan_full_flow_smoke(self, monkeypatch) -> None:
        """完整主流程（非 dry-run、无计划）冒烟：确认 main() 走通到汇总打印。

        通过 mock 掉「全库扫描 + 网络拉取 + 进度文件」相关函数，使流程可在测试环境
        无 DB / 无网络 / 不写生产进度文件跑通，验证 main() 的编排逻辑
        （补历史 + 补今日 + 汇总）无异常。

        注意：``main()`` 实现在 ``scripts.fill_missing.cli`` 中，调用的是该模块
        自己导入的名字，只 patch 兼容层 ``scripts.fill_missing_and_today`` 不会生效，
        会退化成真实网络拉取（每次 45s 超时 + 3×10s 重试）。
        """
        import scripts.fill_missing.cli as cli

        # mock 全库扫描 / 报告 / 进度文件，避免触碰真实 DB、网络与生产进度文件
        monkeypatch.setattr(cli, "get_data_factory", lambda: object())
        monkeypatch.setattr(cli, "fetch_missing_report", lambda: {"today": "2026-10-30"})
        monkeypatch.setattr(cli, "print_missing_report", lambda r: None)
        monkeypatch.setattr(cli, "get_failed_tickers", lambda: [])
        monkeypatch.setattr(cli, "_load_completed_tickers", lambda: set())
        monkeypatch.setattr(cli, "_load_failed_tickers", lambda: set())
        monkeypatch.setattr(cli, "_flush_completed", lambda *a, **k: None)

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

        monkeypatch.setattr(cli, "fetch_full_range", _fake_full)
        monkeypatch.setattr(cli, "fetch_today_incremental", _fake_today)

        # 备份在写入前触发；此处替换掉真实备份，避免触碰 DB（sqlite3.connect 已被 mock）
        backup_calls = {"n": 0}

        def _fake_backup(*args: object, **kwargs: object) -> dict[str, object]:
            backup_calls["n"] += 1
            return {}

        monkeypatch.setattr("scripts.backup_cache.backup_all", _fake_backup)

        # 跑完整主流程（非 dry-run）
        main(plan_path=None, dry_run=False, no_incremental=False)

        # 无失败票、无今日票 → 两个拉取函数都不应被触发
        assert called["full"] == 0
        assert called["today"] == 0
        assert backup_calls["n"] == 1

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

    def test_get_failed_tickers_uses_db(self, monkeypatch, tmp_path) -> None:
        """get_failed_tickers 读取 DB 并按真实 K 线根数判定（非表行数）。"""
        import pickle
        import sqlite3

        import pandas as pd

        from scripts.fill_missing import db_queries

        db = tmp_path / "pipeline_cache.db"
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE kline_cache (ticker TEXT, start TEXT, end TEXT, data BLOB)")
        dense = pd.DataFrame({"close": [1.0] * 120})
        sparse = pd.DataFrame({"close": [1.0] * 5})
        conn.execute(
            "INSERT INTO kline_cache VALUES ('sh.600519', '2023-01-03', '2026-11-19', ?)",
            (pickle.dumps(dense),),
        )
        conn.execute(
            "INSERT INTO kline_cache VALUES ('sz.000001', '2026-10-15', '2026-11-19', ?)",
            (pickle.dumps(sparse),),
        )
        conn.commit()
        conn.close()
        monkeypatch.setattr(db_queries, "CACHE_DB", db)

        assert get_failed_tickers() == ["sz.000001"]


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


class TestFetchTodayIncrementalUsesExpectedTradingDay:
    """fetch_today_incremental 以「应已落库的最近交易日」为终点，而非原始今日。"""

    def test_requests_expected_trading_day_with_lookback(self, monkeypatch) -> None:
        """开盘前/盘中运行时按 expected_trading_day 拉取并回看窗口，避免当日无数据空拉。

        回归：此前用 get_today_date()（原始今日）作为起止，开盘前当日 K 线尚不存在，
        导致全部标的返回空、触发 3 次 × 10s 无谓重试。
        """
        captured: dict[str, str] = {}

        class _RecordingProvider:
            def fetch_kline(
                self,
                ticker: str,
                start_date: str,
                end_date: str,
                frequency: str = "d",
                adjustflag: str = "1",
            ) -> KlineData | None:
                captured["start"] = start_date
                captured["end"] = end_date
                return _make_kline()

        factory = SimpleNamespace(get_provider=lambda name: _RecordingProvider())
        monkeypatch.setattr("scripts.fill_missing.fetch.get_expected_date", lambda: "2026-11-19")
        provider, n_rows, detail = fmat.fetch_today_incremental(factory, "sh.600519", ["rec"])
        assert provider == "rec"
        assert captured["end"] == "2026-11-19"
        assert captured["start"] == "2026-11-09"


class TestRunCliArgs:
    """命令行参数必须真正传到 main()（兼容层曾静默丢弃全部 argv）。"""

    def test_run_cli_forwards_flags_to_main(self, monkeypatch) -> None:
        """run_cli 解析 argv 后以关键字参数调用 main()。"""
        import scripts.fill_missing.cli as cli

        captured: dict[str, object] = {}
        monkeypatch.setattr(cli, "main", lambda **kwargs: captured.update(kwargs))

        cli.run_cli(
            [
                "--plan",
                "plan.json",
                "--dry-run",
                "--no-incremental",
                "--no-backup",
                "--max-retries",
                "2",
                "--retry-delay",
                "5",
            ]
        )

        assert captured == {
            "plan_path": "plan.json",
            "dry_run": True,
            "no_incremental": True,
            "backup": False,
            "max_retries": 2,
            "retry_delay": 5.0,
        }

    def test_run_cli_defaults_when_no_flags(self, monkeypatch) -> None:
        """无参数时使用默认值（备份开启、重试 3 次 / 10 秒）。"""
        import scripts.fill_missing.cli as cli

        captured: dict[str, object] = {}
        monkeypatch.setattr(cli, "main", lambda **kwargs: captured.update(kwargs))

        cli.run_cli([])

        assert captured["plan_path"] is None
        assert captured["backup"] is True
        assert captured["max_retries"] == 3
        assert captured["retry_delay"] == 10.0

    def test_compat_module_main_entry_uses_run_cli(self, monkeypatch) -> None:
        """以脚本方式运行兼容层时走 run_cli，而不是裸 main()（否则 argv 被丢弃）。"""
        import runpy

        import scripts.fill_missing.cli as cli

        called: dict[str, int] = {"n": 0}
        monkeypatch.setattr(cli, "run_cli", lambda argv=None: called.__setitem__("n", called["n"] + 1))
        monkeypatch.setattr("sys.argv", ["fill_missing_and_today.py", "--no-backup"])

        with warnings.catch_warnings():
            # runpy 对「模块已在 sys.modules 中」会发 RuntimeWarning，与本用例无关
            warnings.simplefilter("ignore", RuntimeWarning)
            runpy.run_module("scripts.fill_missing_and_today", run_name="__main__")

        assert called["n"] == 1
