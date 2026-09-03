"""Tests for trade_krono_cli.utils.st_cache — TTL cache decorator.

覆盖缓存命中/失效、不可哈希参数回退、clear() 清理。
"""

from __future__ import annotations

import time

from trade_krono_cli.utils.st_cache import cached

# ═══════════════════════════════════════════════════════
#  Basic caching
# ═══════════════════════════════════════════════════════


class TestCachedBasic:
    def test_cache_hit(self) -> None:
        call_count = 0

        @cached(ttl=60)
        def expensive(x: int) -> int:
            nonlocal call_count
            call_count += 1
            return x * 2

        assert expensive(5) == 10
        assert call_count == 1
        assert expensive(5) == 10  # cache hit
        assert call_count == 1

    def test_different_args_miss(self) -> None:
        call_count = 0

        @cached(ttl=60)
        def fn(x: int) -> int:
            nonlocal call_count
            call_count += 1
            return x

        fn(1)
        fn(2)
        assert call_count == 2

    def test_kwargs_cached_separately(self) -> None:
        call_count = 0

        @cached(ttl=60)
        def fn(x: int, y: int = 0) -> int:
            nonlocal call_count
            call_count += 1
            return x + y

        fn(1, y=10)
        fn(1, y=20)
        assert call_count == 2

    def test_ttls_zero_means_immediate_expiry(self) -> None:
        """TTL=0 应每次重新计算（无缓存）。"""
        call_count = 0

        @cached(ttl=0)
        def fn(x: int) -> int:
            nonlocal call_count
            call_count += 1
            return x

        fn(1)
        fn(1)
        assert call_count == 2


# ═══════════════════════════════════════════════════════
#  TTL expiry
# ═══════════════════════════════════════════════════════


class TestTTLExpiry:
    def test_expires_after_ttl(self) -> None:
        call_count = 0

        @cached(ttl=0.05)  # 50ms
        def fn(x: int) -> int:
            nonlocal call_count
            call_count += 1
            return x * 3

        fn(10)
        assert call_count == 1
        time.sleep(0.1)
        fn(10)
        assert call_count == 2

    def test_still_valid_within_ttl(self) -> None:
        call_count = 0

        @cached(ttl=1.0)
        def fn(x: int) -> int:
            nonlocal call_count
            call_count += 1
            return x + 1

        fn(5)
        time.sleep(0.05)
        fn(5)
        assert call_count == 1


# ═══════════════════════════════════════════════════════
#  Unhashable args fallback
# ═══════════════════════════════════════════════════════


class TestUnhashableFallback:
    def test_list_arg_fallback(self) -> None:
        call_count = 0

        @cached(ttl=60)
        def fn(items: list[int]) -> int:
            nonlocal call_count
            call_count += 1
            return sum(items)

        assert fn([1, 2, 3]) == 6
        assert call_count == 1
        # 第二次调用应也正常执行（无缓存，但不会报错）
        assert fn([1, 2, 3]) == 6
        assert call_count == 2

    def test_dict_arg_fallback(self) -> None:
        call_count = 0

        @cached(ttl=60)
        def fn(data: dict[str, int]) -> int:
            nonlocal call_count
            call_count += 1
            return len(data)

        assert fn({"a": 1}) == 1
        assert call_count == 1

    def test_dataclass_arg_fallback(self) -> None:
        from dataclasses import dataclass

        @dataclass
        class Point:
            x: int
            y: int

        call_count = 0

        @cached(ttl=60)
        def fn(p: Point) -> int:
            nonlocal call_count
            call_count += 1
            return p.x + p.y

        # dataclass 默认可哈希（若所有字段可哈希），这里测试不可哈希情况
        # 若未抛出异常即为通过
        fn(Point(1, 2))
        assert call_count == 1


# ═══════════════════════════════════════════════════════
#  clear()
# ═══════════════════════════════════════════════════════


class TestClear:
    def test_clear_forces_recompute(self) -> None:
        call_count = 0

        @cached(ttl=60)
        def fn(x: int) -> int:
            nonlocal call_count
            call_count += 1
            return x * 10

        fn(5)
        assert call_count == 1
        fn.clear()
        fn(5)
        assert call_count == 2

    def test_clear_multiple_calls(self) -> None:
        call_count = 0

        @cached(ttl=60)
        def fn(x: int) -> int:
            nonlocal call_count
            call_count += 1
            return x

        fn(1)
        fn(2)
        assert call_count == 2
        fn.clear()
        fn(1)
        fn(2)
        assert call_count == 4


# ═══════════════════════════════════════════════════════
#  functools.wraps preservation
# ═══════════════════════════════════════════════════════


class TestWrapsPreservation:
    def test_name_preserved(self) -> None:
        @cached(ttl=60)
        def my_func(x: int) -> int:
            return x

        assert my_func.__name__ == "my_func"

    def test_doc_preserved(self) -> None:
        @cached(ttl=60)
        def documented(x: int) -> int:
            """My docstring."""
            return x

        assert "My docstring" in documented.__doc__
