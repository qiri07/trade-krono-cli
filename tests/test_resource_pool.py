"""测试 ResourcePool — 统一并发资源管理器。"""
import asyncio
import pytest
from concurrent.futures import Future
from trade_krono_cli.pipeline.resource_pool import (
    ResourcePool, PoolConfig, get_pool, clear_pool_singleton,
)


class TestResourcePool:
    """ResourcePool 基础功能测试。"""

    def setup_method(self):
        clear_pool_singleton()

    def teardown_method(self):
        clear_pool_singleton()

    def test_submit_cpu(self):
        """submit_cpu 应提交任务并返回 Future。"""
        pool = ResourcePool()
        fut = pool.submit_cpu(lambda: 42)
        assert fut.result() == 42
        pool.close()

    def test_submit_io(self):
        """submit_io 应提交 IO 任务。"""
        pool = ResourcePool()
        fut = pool.submit_io(lambda: "io_result")
        assert fut.result() == "io_result"
        pool.close()

    def test_run_parallel(self):
        """run_parallel 应并行执行多个 callable。"""
        pool = ResourcePool()
        results = pool.run_parallel([
            lambda: 1,
            lambda: 2,
            lambda: 3,
        ])
        assert sorted(results) == [1, 2, 3]
        pool.close()

    def test_run_parallel_empty(self):
        """空列表应返回空列表。"""
        pool = ResourcePool()
        assert pool.run_parallel([]) == []
        pool.close()

    def test_run_parallel_with_exception(self):
        """单个 callable 抛异常不应中断其他任务。"""
        pool = ResourcePool()

        def good():
            return "ok"

        def bad():
            raise ValueError("intentional failure")

        results = pool.run_parallel([good, bad, good])
        assert results[0] == "ok"
        assert results[1] is None
        assert results[2] == "ok"
        pool.close()

    def test_config_custom_workers(self):
        """自定义 PoolConfig 应生效。"""
        config = PoolConfig(cpu_workers=4, io_workers=2)
        pool = ResourcePool(config)
        assert pool._config.cpu_workers == 4
        assert pool._config.io_workers == 2
        pool.close()

    def test_config_llm_and_gpu_defaults(self):
        """PoolConfig 默认值应包含 LLM 和 GPU 配置。"""
        config = PoolConfig()
        assert config.llm_concurrency == 3
        assert config.gpu_queue_size == 1
        pool = ResourcePool(config)
        pool.close()

    def test_singleton_get_pool(self):
        """get_pool() 应返回单例。"""
        p1 = get_pool()
        p2 = get_pool()
        assert p1 is p2
        p1.close()
        clear_pool_singleton()

    def test_context_manager(self):
        """with 语句应自动关闭池。"""
        with ResourcePool() as pool:
            fut = pool.submit_cpu(lambda: "done")
            assert fut.result() == "done"
        # 关闭后线程池进入终止状态，submit 会抛 BrokenThreadPoolError
        # 这里验证的是 close() 能被安全调用且不再重新创建池
        pool.close()  # 二次 close 不应抛异常
        pool.close()  # 三次也安全

    def test_separate_pools_are_independent(self):
        """不同 ResourcePool 实例应有独立的线程池。"""
        pool_a = ResourcePool()
        pool_b = ResourcePool()
        assert pool_a.cpu is not pool_b.cpu
        pool_a.close()
        pool_b.close()

    def test_llm_semaphore_lazy_init(self):
        """LLM 信号量应在首次访问时懒初始化。"""
        pool = ResourcePool()
        assert pool._llm_semaphore is None
        sem = pool._get_llm_semaphore()
        assert sem is not None
        assert isinstance(sem, asyncio.Semaphore)
        assert sem._value == pool._config.llm_concurrency
        pool.close()

    def test_gpu_semaphore_lazy_init(self):
        """GPU 信号量应在首次访问时懒初始化。"""
        pool = ResourcePool()
        assert pool._gpu_semaphore is None
        sem = pool._get_gpu_semaphore()
        assert sem is not None
        assert isinstance(sem, asyncio.Semaphore)
        assert sem._value == pool._config.gpu_queue_size
        pool.close()

    def test_llm_async_context(self):
        """LLM 异步上下文管理器应正确限制并发。"""
        pool = ResourcePool(config=PoolConfig(llm_concurrency=2))
        max_concurrent = 0
        current = 0

        async def tracked_task():
            nonlocal max_concurrent, current
            async with pool.llm():
                current += 1
                max_concurrent = max(max_concurrent, current)
                await asyncio.sleep(0.01)
                current -= 1

        async def _run():
            await asyncio.gather(*[tracked_task() for _ in range(6)])

        asyncio.run(_run())
        assert max_concurrent <= 2
        pool.close()

    def test_gpu_async_context(self):
        """GPU 异步上下文管理器应正确限制并发（queue_size=1）。"""
        pool = ResourcePool(config=PoolConfig(gpu_queue_size=1))
        max_concurrent = 0
        current = 0

        async def tracked_task():
            nonlocal max_concurrent, current
            async with pool.gpu():
                current += 1
                max_concurrent = max(max_concurrent, current)
                await asyncio.sleep(0.01)
                current -= 1

        async def _run():
            await asyncio.gather(*[tracked_task() for _ in range(4)])

        asyncio.run(_run())
        assert max_concurrent <= 1
        pool.close()


class TestDeprecatedBatchRunner:
    """
    BatchRunner（async）已停止使用，保留测试确保不被破坏。
    生产代码应使用 ResourcePool。
    """

    def test_batch_runner_still_importable(self):
        """BatchRunner 仍可导入（向后兼容）。"""
        from trade_krono_cli.batch.batch_runner import BatchRunner
        runner = BatchRunner()
        result = runner.run_sync([], lambda x: x)
        assert result == []
