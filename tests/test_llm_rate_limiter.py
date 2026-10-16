"""llm_rate_limiter 模块测试。

覆盖：
- get_request_stats 结构
- reset_rate_limit 清空
- wait_for_rate_limit 最小间隔（monkeypatch MIN_REQUEST_DELAY 加速）
- 窗口限流（monkeypatch WINDOW_SECONDS / MAX_REQUESTS_PER_WINDOW）
"""

from __future__ import annotations

from unittest.mock import patch

import trade_krono_cli.llm_rate_limiter as rlm


def _fresh() -> None:
    rlm.reset_rate_limit()


class TestGetRequestStats:
    def test_stats_structure_when_empty(self) -> None:
        _fresh()
        stats = rlm.get_request_stats()
        assert stats["requests_in_window"] == 0
        assert stats["max_per_window"] == rlm.MAX_REQUESTS_PER_WINDOW
        assert stats["min_delay"] == rlm.MIN_REQUEST_DELAY
        assert stats["window_seconds"] == rlm.WINDOW_SECONDS

    def test_stats_counts_recent_requests(self) -> None:
        _fresh()
        rlm.wait_for_rate_limit()
        stats = rlm.get_request_stats()
        assert stats["requests_in_window"] == 1


class TestReset:
    def test_reset_clears_log(self) -> None:
        _fresh()
        with patch.object(rlm, "MIN_REQUEST_DELAY", 0.0):
            rlm.wait_for_rate_limit()
        assert len(rlm._request_log) == 1
        rlm.reset_rate_limit()
        assert len(rlm._request_log) == 0


class TestMinDelay:
    def test_min_delay_enforced(self) -> None:
        """第二次请求需等待 min_delay - 已耗时。"""
        _fresh()
        with patch.object(rlm, "MIN_REQUEST_DELAY", 0.3):
            rlm.wait_for_rate_limit()
            sleep_calls: list[float] = []
            with patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)):
                rlm.wait_for_rate_limit()
        assert len(sleep_calls) == 1
        assert 0.0 < sleep_calls[0] <= 0.3

    def test_second_call_no_wait_when_enough_elapsed(self) -> None:
        """间隔已大于 min_delay 时不触发 sleep。"""
        _fresh()
        with patch.object(rlm, "MIN_REQUEST_DELAY", 0.01):
            rlm.wait_for_rate_limit()
            import time as _time

            _time.sleep(0.02)
            sleep_calls: list[float] = []
            with patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)):
                rlm.wait_for_rate_limit()
        assert sleep_calls == []


class TestWindowLimit:
    def test_window_full_triggers_sleep(self) -> None:
        """窗口内请求数达到上限时，等待最早请求滑出窗口。"""
        _fresh()
        with patch.object(rlm, "WINDOW_SECONDS", 0.1):
            with patch.object(rlm, "MIN_REQUEST_DELAY", 0.0):
                rlm.wait_for_rate_limit()
            # 直接塞满窗口
            import time as _time

            now = _time.monotonic()
            for _ in range(rlm.MAX_REQUESTS_PER_WINDOW):
                rlm._request_log.append(now - 0.05)

            sleep_calls: list[float] = []
            with patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)):
                rlm.wait_for_rate_limit()

            # 至少 1 次窗口等待；窗口等待后可能再叠加 1 次最小间隔等待
            assert len(sleep_calls) in (1, 2)
            # 第一次应等待最早记录滑出 0.1s 窗口（≈ 0.05s 起）
            assert sleep_calls[0] > 0

    def test_window_expiry_clears_old_records(self) -> None:
        """超过窗口期的记录被清理，不触发等待。"""
        _fresh()
        import time as _time

        now = _time.monotonic()
        for _ in range(rlm.MAX_REQUESTS_PER_WINDOW):
            rlm._request_log.append(now - rlm.WINDOW_SECONDS - 1.0)

        with patch.object(rlm, "MIN_REQUEST_DELAY", 0.0):
            sleep_calls: list[float] = []
            with patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)):
                rlm.wait_for_rate_limit()
        assert sleep_calls == []
        # 旧记录被清理，只剩本次
        assert len(rlm._request_log) == 1


class TestThreadSafety:
    def test_concurrent_requests_all_logged(self) -> None:
        """多线程并发调用后，日志条数等于线程数。"""
        import threading

        _fresh()
        n_threads = 8
        with patch.object(rlm, "MIN_REQUEST_DELAY", 0.0):
            with patch.object(rlm, "MAX_REQUESTS_PER_WINDOW", 1000):
                with patch.object(rlm, "WINDOW_SECONDS", 3600.0):
                    def worker() -> None:
                        rlm.wait_for_rate_limit()

                    threads = [threading.Thread(target=worker) for _ in range(n_threads)]
                    for t in threads:
                        t.start()
                    for t in threads:
                        t.join()
        assert len(rlm._request_log) == n_threads
