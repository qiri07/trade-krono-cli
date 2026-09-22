"""Tests for trade_krono_cli.utils.st_cache — TTL cache decorator.

覆盖缓存命中/失效、不可哈希参数回退、clear() 清理。
"""

from __future__ import annotations

import time
from unittest.mock import patch

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
        fn.clear()  # type: ignore[attr-defined]
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
        fn.clear()  # type: ignore[attr-defined]
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

        assert "My docstring" in (documented.__doc__ or "")


# ═══════════════════════════════════════════
#  Truly unhashable args (lines 38-40)
# ═══════════════════════════════════════════


class TestUnhashableArgs:
    def test_truly_unhashable_class_fallback(self) -> None:
        """自定义不可哈希类（无__hash__）触发fallback分支。"""
        call_count = 0

        class UnhashableObj:
            def __init__(self, value: int) -> None:
                self.value = value

            def __eq__(self, other: object) -> bool:
                return isinstance(other, UnhashableObj) and self.value == other.value

            def __repr__(self) -> str:
                return f"UnhashableObj({self.value})"

            __hash__ = None  # 显式设为不可哈希

        @cached(ttl=60)
        def fn(obj: UnhashableObj) -> int:
            nonlocal call_count
            call_count += 1
            return obj.value

        result = fn(UnhashableObj(42))
        assert result == 42
        assert call_count == 1
        # 第二次调用因不可哈希而回退到直接调用
        result2 = fn(UnhashableObj(42))
        assert result2 == 42
        assert call_count == 2

    def test_unorderable_kwargs_values_fallback(self) -> None:
        """sorted() 抛 TypeError 时触发 fallback（lines 38-40）。"""
        call_count = 0

        @cached(ttl=60)
        def fn(x: int, **kwargs: object) -> int:
            nonlocal call_count
            call_count += 1
            return x

        # 用 mock 模拟 sorted 抛出 TypeError（真实场景中 kwargs 值类型不可比较会触发）
        with patch("trade_krono_cli.utils.st_cache.sorted") as mock_sorted:
            mock_sorted.side_effect = TypeError("unorderable")
            result = fn(1, a=1, b="hello")
        assert result == 1
        assert call_count == 1

    def test_unhashable_return_value_fallback(self) -> None:
        """返回不可哈希对象时，值仍可存入缓存（dict value无限制），命中缓存返回同一对象。"""
        call_count = 0

        @cached(ttl=60)
        def fn(x: int) -> list:
            nonlocal call_count
            call_count += 1
            return [x]

        result = fn(1)
        assert result == [1]
        assert call_count == 1
        # 第二次调用应命中缓存（key可哈希，value不可哈希不影响存储）
        result2 = fn(1)
        assert result2 == [1]
        assert call_count == 1  # 未重新调用
        assert result is result2  # 同一对象
