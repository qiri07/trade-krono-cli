"""
ResourcePool — 统一的并发资源管理器。

职责：
  · CPU 线程池   — TA 分析、数据预处理（CPU-bound）
  · IO 线程池    — 网络请求、文件读写（IO-bound）
  · LLM 信号量   — LLM API 并发限流（异步场景）
  · GPU 队列     — Kronos 推理并发控制（由 Session 内部管理）

设计原则：
  · 所有 ThreadPoolExecutor / Semaphore 均由此处统一创建和关闭
  · QuantPipeline / StreamPipeline 通过 ResourcePool 提交任务
  · 禁止在各业务模块中直接 new ThreadPoolExecutor 或 asyncio.Semaphore

机器配置参考（Ryzen 7 4800H / 64GB / Quadro P620 4GB）：
  · CPU 线程池：8 worker（匹配物理核心，TA 分析为 CPU-bound）
  · IO 线程池：4 worker（baostock / akshare 网络 I/O）
  · LLM 并发：3 worker（防 API rate limit）
  · GPU 队列：1 depth（Quadro P620 4GB 显存限制）

架构：
  Scheduler
      │
      ▼
  Task Executor
      │
      ├── CPU Pool       (ThreadPoolExecutor, 8 workers)
      ├── IO Pool        (ThreadPoolExecutor, 4 workers)
      ├── LLM Semaphore  (asyncio.Semaphore, 3 concurrent)
      └── GPU Queue      (asyncio.Semaphore, 1 depth)
"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable, Optional

from loguru import logger


@dataclass(frozen=True)
class PoolConfig:
    """线程池资源配置。"""

    cpu_workers: int = 8
    """CPU-bound 任务的最大并发数（默认 8，匹配 Ryzen 7 4800H 物理核心）。"""

    io_workers: int = 4
    """IO-bound 任务的最大并发数（默认 4，适合 baostock/akshare 网络延迟）。"""

    llm_concurrency: int = 3
    """LLM API 最大并发请求数（默认 3，防 rate limit）。"""

    gpu_queue_size: int = 1
    """GPU 推理队列深度（默认 1，Quadro P620 4GB 显存限制）。"""


class _AsyncSemaphoreContext:
    """包装 asyncio.Semaphore 的 async context manager。"""

    __slots__ = ("_sem",)

    def __init__(self, sem: asyncio.Semaphore):
        self._sem = sem

    async def __aenter__(self):
        await self._sem.acquire()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self._sem.release()
        return False


class ResourcePool:
    """
    统一并发资源管理器。

    用法：
        pool = ResourcePool()
        # 提交 CPU 任务
        fut = pool.submit_cpu(fn, arg1, arg2)
        # 提交 IO 任务
        fut = pool.submit_io(fn, url)
        # 并行等待
        results = pool.run_parallel([fn1, fn2, fn3])
        # LLM 并发限流（异步）
        async with pool.llm():
            await call_llm(...)
        # GPU 推理排队（异步）
        async with pool.gpu():
            await run_gpu_inference(...)
        # 关闭时释放所有线程
        pool.close()
    """

    def __init__(self, config: Optional[PoolConfig] = None):
        self._config = config or PoolConfig()
        self._cpu_pool: Optional[ThreadPoolExecutor] = None
        self._io_pool: Optional[ThreadPoolExecutor] = None
        # LLM / GPU 信号量（懒初始化，异步上下文使用）
        self._llm_semaphore: Optional[asyncio.Semaphore] = None
        self._gpu_semaphore: Optional[asyncio.Semaphore] = None
        self._llm_lock = threading.Lock()
        self._gpu_lock = threading.Lock()

    @property
    def cpu(self) -> ThreadPoolExecutor:
        """懒初始化 CPU 线程池。"""
        if self._cpu_pool is None:
            self._cpu_pool = ThreadPoolExecutor(
                max_workers=self._config.cpu_workers,
                thread_name_prefix="cpu",
            )
        return self._cpu_pool

    @property
    def io(self) -> ThreadPoolExecutor:
        """懒初始化 IO 线程池。"""
        if self._io_pool is None:
            self._io_pool = ThreadPoolExecutor(
                max_workers=self._config.io_workers,
                thread_name_prefix="io",
            )
        return self._io_pool

    def _get_llm_semaphore(self) -> asyncio.Semaphore:
        """懒初始化 LLM 信号量（线程安全）。"""
        if self._llm_semaphore is None:
            with self._llm_lock:
                if self._llm_semaphore is None:
                    self._llm_semaphore = asyncio.Semaphore(self._config.llm_concurrency)
        return self._llm_semaphore

    def _get_gpu_semaphore(self) -> asyncio.Semaphore:
        """懒初始化 GPU 信号量（线程安全）。"""
        if self._gpu_semaphore is None:
            with self._gpu_lock:
                if self._gpu_semaphore is None:
                    self._gpu_semaphore = asyncio.Semaphore(self._config.gpu_queue_size)
        return self._gpu_semaphore

    def submit_cpu(self, fn: Callable, *args, **kwargs) -> Future:
        """
        提交 CPU-bound 任务（TA 分析、数据预处理）。

        Returns
        -------
        concurrent.futures.Future
        """
        return self.cpu.submit(fn, *args, **kwargs)

    def submit_io(self, fn: Callable, *args, **kwargs) -> Future:
        """
        提交 IO-bound 任务（网络请求、文件读写）。

        Returns
        -------
        concurrent.futures.Future
        """
        return self.io.submit(fn, *args, **kwargs)

    def run_parallel(
        self,
        callables: list[Callable],
        *,
        timeout: Optional[float] = None,
    ) -> list:
        """
        并行执行多个无参 callable，收集结果。

        异常被捕获并转为 None 返回，不中断其他任务。

        Parameters
        ----------
        callables : 待执行的函数列表（0 参数）
        timeout   : 整体超时（秒），None 表示不限制

        Returns
        -------
        list : 每个 callable 的结果（失败项为 None）
        """
        if not callables:
            return []

        futures: dict[Future, int] = {}
        for i, fn in enumerate(callables):
            fut = self.cpu.submit(fn)
            futures[fut] = i

        results: list = [None] * len(callables)
        for fut in as_completed(futures, timeout=timeout):
            idx = futures[fut]
            try:
                results[idx] = fut.result()
            except Exception as e:
                logger.warning(f"⚠️  任务 {idx} 异常: {e}")
                results[idx] = None

        return results

    def llm(self):
        """
        LLM API 并发限流异步上下文管理器。

        在异步代码中使用：
            async with pool.llm():
                result = await call_llm_api(...)
        """
        return _AsyncSemaphoreContext(self._get_llm_semaphore())

    def gpu(self):
        """
        GPU 推理排队异步上下文管理器。

        在异步代码中使用：
            async with pool.gpu():
                result = await run_gpu_inference(...)
        """
        return _AsyncSemaphoreContext(self._get_gpu_semaphore())

    def close(self) -> None:
        """关闭所有线程池，等待任务完成。"""
        for name, pool in [("cpu", self._cpu_pool), ("io", self._io_pool)]:
            if pool is not None:
                pool.shutdown(wait=True)
                logger.debug(f"🧹 ResourcePool.{name} 已关闭")
        self._cpu_pool = None
        self._io_pool = None
        with self._llm_lock:
            self._llm_semaphore = None
        with self._gpu_lock:
            self._gpu_semaphore = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


# ── 模块级单例（向后兼容）──────────────────────────────────────────────────────

_pool: Optional[ResourcePool] = None


def get_pool() -> ResourcePool:
    """获取全局 ResourcePool 单例。"""
    global _pool
    if _pool is None:
        _pool = ResourcePool()
    return _pool


def clear_pool_singleton() -> None:
    """清除单例，用于测试隔离。"""
    global _pool
    if _pool is not None:
        _pool.close()
    _pool = None
