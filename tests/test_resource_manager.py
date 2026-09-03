"""测试 ResourceManager — 统一资源管理器。"""

import asyncio
import time
from typing import NoReturn

import pytest

from trade_krono_cli.pipeline.resource_manager import (
    GpuQueue,
    LlmSemaphore,
    ResourceBudget,
    ResourceManager,
    clear_manager,
    get_manager,
)


class TestResourceBudget:
    """ResourceBudget 默认值测试。"""

    def test_defaults(self) -> None:
        b = ResourceBudget()
        assert b.cpu_workers == 8
        assert b.io_workers == 4
        assert b.gpu_queue_size == 1
        assert b.llm_concurrency == 3
        assert b.llm_min_interval == 1.0

    def test_custom_values(self) -> None:
        b = ResourceBudget(cpu_workers=16, io_workers=8, gpu_queue_size=2, llm_concurrency=5)
        assert b.cpu_workers == 16
        assert b.io_workers == 8
        assert b.gpu_queue_size == 2
        assert b.llm_concurrency == 5


class TestGpuQueue:
    """GpuQueue 单元测试。"""

    def test_acquire_release(self) -> None:
        q = GpuQueue(max_concurrency=1)
        q.acquire()
        assert q.in_flight == 1
        q.release()
        assert q.in_flight == 0

    def test_context_manager_sync(self) -> None:
        q = GpuQueue(max_concurrency=1)
        with q:
            assert q.in_flight == 1
        assert q.in_flight == 0

    def test_concurrency_limit(self) -> None:
        """超过 max_concurrency 时 acquire 应阻塞直到 release。"""
        q = GpuQueue(max_concurrency=2)
        results = []

        def task(name) -> None:
            with q:
                results.append(f"{name}_in")
                time.sleep(0.05)
                results.append(f"{name}_out")

        import threading

        t1 = threading.Thread(target=task, args=("a",))
        t2 = threading.Thread(target=task, args=("b",))
        t3 = threading.Thread(target=task, args=("c",))
        t1.start()
        t2.start()
        t3.start()
        t1.join()
        t2.join()
        t3.join()

        # 同一时刻最多 2 个任务在飞
        assert results.count("a_in") == 1
        assert results.count("a_out") == 1
        assert len(results) == 6  # 3 tasks × 2 events

    def test_stats(self) -> None:
        q = GpuQueue(max_concurrency=1)
        q.acquire()
        s = q.stats()
        assert s["in_flight"] == 1
        assert s["max_concurrency"] == 1
        assert s["total_acquired"] == 1
        q.release()


class TestLlmSemaphore:
    """LlmSemaphore 单元测试。"""

    def test_acquire_release(self) -> None:
        sem = LlmSemaphore(max_concurrency=3, min_interval=0.0)
        sem.acquire("deepseek")
        stats = sem.stats()
        assert stats["total_acquired"] == 1
        assert stats["active_requests"]["deepseek"] == 1
        sem.release("deepseek")
        assert sem.stats()["active_requests"].get("deepseek", 0) == 0

    def test_sliding_window_throttle(self) -> None:
        """滑动窗口应限制短时间内的大量请求。"""
        sem = LlmSemaphore(max_concurrency=2, min_interval=0.1)
        start = time.time()
        # 发送 3 个请求，第 3 个应该等待
        sem.acquire("test")
        sem.acquire("test")
        sem.acquire("test")  # 应触发 throttling
        elapsed = time.time() - start
        assert elapsed >= 0.09  # 至少等了一个窗口周期
        sem.release("test")
        sem.release("test")
        sem.release("test")

    def test_provider_isolation(self) -> None:
        """不同 provider 应有独立的滑动窗口。"""
        sem = LlmSemaphore(max_concurrency=1, min_interval=0.0)
        sem.acquire("provider_a")
        sem.acquire("provider_b")  # 应立即可行（独立窗口）
        stats = sem.stats()
        assert stats["active_requests"]["provider_a"] == 1
        assert stats["active_requests"]["provider_b"] == 1
        sem.release("provider_a")
        sem.release("provider_b")

    def test_stats_empty(self) -> None:
        sem = LlmSemaphore(max_concurrency=3, min_interval=1.0)
        s = sem.stats()
        assert s["max_concurrency"] == 3
        assert s["min_interval_sec"] == 1.0
        assert s["total_acquired"] == 0


class TestResourceManager:
    """ResourceManager 集成测试。"""

    def setup_method(self) -> None:
        clear_manager()

    def teardown_method(self) -> None:
        clear_manager()

    def test_singleton(self) -> None:
        m1 = get_manager()
        m2 = get_manager()
        assert m1 is m2
        m1.close()

    def test_submit_cpu(self) -> None:
        m = get_manager()
        fut = m.submit_cpu(lambda: 42)
        assert fut.result() == 42
        m.close()

    def test_submit_io(self) -> None:
        m = get_manager()
        fut = m.submit_io(lambda: "io")
        assert fut.result() == "io"
        m.close()

    def test_submit_invalid_category(self) -> None:
        m = get_manager()
        with pytest.raises(ValueError, match="未知资源类别"):
            m.submit("gpu", lambda: 1)
        m.close()

    def test_run_parallel(self) -> None:
        m = get_manager()
        results = m.run_parallel([lambda: 1, lambda: 2, lambda: 3])
        assert sorted(results) == [1, 2, 3]
        m.close()

    def test_run_parallel_with_exception(self) -> None:
        m = get_manager()

        def bad() -> NoReturn:
            msg = "fail"
            raise RuntimeError(msg)

        results = m.run_parallel([lambda: "ok", bad, lambda: "also_ok"])
        assert results[0] == "ok"
        assert results[1] is None
        assert results[2] == "also_ok"
        m.close()

    def test_gpu_enter_exit(self) -> None:
        m = get_manager()
        m.gpu_enter()
        assert m.gpu.in_flight == 1
        m.gpu_exit()
        assert m.gpu.in_flight == 0
        m.close()

    def test_gpu_context_manager(self) -> None:
        m = get_manager()
        with m.gpu:  # gpu 是属性，不是方法
            assert m.gpu.in_flight == 1
        assert m.gpu.in_flight == 0
        m.close()

    def test_gpu_async_context(self) -> None:
        m = get_manager()

        async def _run() -> None:
            async with m.gpu:
                assert m.gpu.in_flight == 1
            assert m.gpu.in_flight == 0

        asyncio.run(_run())
        m.close()

    def test_llm_acquire_release(self) -> None:
        m = get_manager()
        m.llm.acquire("deepseek")
        assert m.llm.stats()["total_acquired"] == 1
        m.llm.release("deepseek")
        m.close()

    def test_stats(self) -> None:
        m = get_manager()
        s = m.stats()
        assert s["budget"]["cpu_workers"] == 8
        assert s["budget"]["gpu_queue_size"] == 1
        assert s["gpu"]["in_flight"] == 0
        assert s["closed"] is False
        m.close()

    def test_describe(self) -> None:
        m = get_manager()
        desc = m.describe()
        assert "8CPU" in desc
        assert "4IO" in desc
        assert "1GPU" in desc
        assert "3LLM" in desc
        m.close()

    def test_context_manager(self) -> None:
        with ResourceManager() as m:
            fut = m.submit_cpu(lambda: "done")
            assert fut.result() == "done"
        assert m._closed

    def test_double_close_safe(self) -> None:
        m = get_manager()
        m.close()
        m.close()  # 不应抛异常
        assert m._closed

    def test_custom_budget(self) -> None:
        budget = ResourceBudget(cpu_workers=4, io_workers=2, gpu_queue_size=2, llm_concurrency=5)
        m = ResourceManager(budget)
        assert m.stats()["budget"]["cpu_workers"] == 4
        assert m.stats()["budget"]["gpu_queue_size"] == 2
        m.close()


class TestBackwardCompat:
    """向后兼容：get_pool() / clear_pool_singleton() 应仍可用。"""

    def setup_method(self) -> None:
        clear_manager()

    def teardown_method(self) -> None:
        clear_manager()

    def test_get_pool_returns_manager(self) -> None:
        from trade_krono_cli.pipeline.resource_manager import get_pool

        p = get_pool()
        assert hasattr(p, "submit_cpu")
        assert hasattr(p, "submit_io")
        assert hasattr(p, "gpu")
        assert hasattr(p, "llm")
        p.close()

    def test_clear_pool_singleton(self) -> None:
        from trade_krono_cli.pipeline.resource_manager import clear_pool_singleton

        m1 = get_manager()
        clear_pool_singleton()
        m2 = get_manager()
        assert m1 is not m2  # 新实例
