"""tests for scripts.fill_missing_and_today — 补拉计划读取与 main 参数。"""

from __future__ import annotations

import json

import scripts.fill_missing_and_today as fmat
from scripts.fill_missing_and_today import get_failed_tickers, load_sync_plan, main


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
