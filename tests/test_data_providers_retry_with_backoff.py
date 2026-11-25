"""trade_krono_cli.data_providers.retry_with_backoff 单元测试。

覆盖 retry_with_backoff 装饰器的指数退避、可重试异常、on_retry 回调、
以及 retry_kline_fetch 专用装饰器。使用 unittest.mock 模拟时间与失败。
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from trade_krono_cli.data_providers.retry_with_backoff import retry_kline_fetch, retry_with_backoff


class TestRetryWithBackoff:
    """retry_with_backoff 装饰器核心行为测试。"""

    def test_success_first_attempt_no_retry(self) -> None:
        calls: list[int] = []

        @retry_with_backoff(max_retries=3, base_delay=0.01, jitter=False)
        def func() -> int:
            calls.append(1)
            return 42

        assert func() == 42
        assert calls == [1]

    def test_retries_then_succeeds(self) -> None:
        state = SimpleNamespace(attempt=0)

        @retry_with_backoff(max_retries=3, base_delay=0.01, jitter=False)
        def func() -> int:
            state.attempt += 1
            if state.attempt < 3:
                raise ValueError("transient")
            return state.attempt

        with patch("time.sleep"):
            assert func() == 3
        assert state.attempt == 3

    def test_exhausts_retries_then_raises(self) -> None:
        state = SimpleNamespace(attempt=0)

        @retry_with_backoff(max_retries=2, base_delay=0.01, jitter=False)
        def func() -> int:
            state.attempt += 1
            raise RuntimeError("persistent")

        with patch("time.sleep"), pytest.raises(RuntimeError, match="persistent"):
            func()
        # 初始调用 + max_retries 次重试 = 3 次
        assert state.attempt == 3

    def test_non_retryable_exception_not_retried(self) -> None:
        state = SimpleNamespace(attempt=0)

        @retry_with_backoff(max_retries=3, base_delay=0.01, jitter=False, retry_on=(ValueError,))
        def func() -> int:
            state.attempt += 1
            raise TypeError("not retried")

        with pytest.raises(TypeError, match="not retried"):
            func()
        assert state.attempt == 1

    def test_on_retry_callback_invoked(self) -> None:
        state = SimpleNamespace(attempt=0, cb_calls=0)

        def on_retry(attempt: int, exception: Exception) -> None:
            state.cb_calls += 1
            assert isinstance(exception, ValueError)

        @retry_with_backoff(max_retries=2, base_delay=0.01, jitter=False, on_retry=on_retry)
        def func() -> int:
            state.attempt += 1
            raise ValueError("transient")

        with patch("time.sleep"), pytest.raises(ValueError):
            func()
        assert state.attempt == 3
        assert state.cb_calls == 2


class TestRetryKlineFetch:
    """retry_kline_fetch 专用装饰器测试。"""

    def test_retry_kline_fetch_defaults(self) -> None:
        state = SimpleNamespace(attempt=0)

        @retry_kline_fetch(max_retries=2, base_delay=0.01, jitter=False)
        def func() -> str:
            state.attempt += 1
            if state.attempt < 2:
                raise ConnectionError("network")
            return "ok"

        with patch("time.sleep"):
            assert func() == "ok"
        assert state.attempt == 2


class TestDecoratorPreservesMetadata:
    """装饰器不应破坏原函数元数据与签名。"""

    def test_wraps_preserves_name_and_doc(self) -> None:
        @retry_with_backoff(max_retries=1, base_delay=0.01, jitter=False)
        def original_func() -> int:
            """Original docstring."""
            return 1

        assert original_func.__name__ == "original_func"
        assert original_func.__doc__ == "Original docstring."
