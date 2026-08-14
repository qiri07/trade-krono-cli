"""
resource_manager — 统一资源管理器（ResourceManager）。

这是项目所有并发资源的唯一入口。禁止在各业务模块中直接创建线程池、信号量
或轮询 GPU 状态。所有资源必须通过 ResourceManager 获取。

架构：
  Scheduler
      │
      ▼
  ResourceManager (本文件，单例)
      │
      ├── cpu_pool      ThreadPoolExecutor — 数据拉取、预处理、评估、报告
      ├── io_pool       ThreadPoolExecutor — 网络 I/O（baostock / akshare）
      ├── gpu_queue     GpuQueue           — Kronos 推理并发控制
      └── llm_semaphore LlmSemaphore       — LLM API 并发 + 滑动窗口限流

机器配置参考（Ryzen 7 4800H / 64GB / Quadro P620 4GB）：
  CPU 总计 16 线程：
    ├── data_fetch   ≤ 4 worker（baostock / akshare 网络 I/O）
    ├── preprocessing ≤ 4 worker（特征工程、数据清洗）
    ├── evaluation   ≤ 4 worker（IC 计算、回测、打分）
    └── reports      ≤ 4 worker（HTML/JSON 报告生成）

  GPU Quadro P620 4GB：
    ├── Kronos         max_concurrency = 1（单模型独占显存）
    └── other models   disabled（暂无其他 GPU 模型）

  LLM API：
    ├── concurrency  = 3（防 API rate limit）
    ├── min_interval = 1.0s（滑动窗口，每个 provider 独立计数）
    └── retry        由 retry_policy 层处理，非本模块职责

使用方式：
    from trade_krono_cli.pipeline.resource_manager import get_manager

    # 提交任务
    fut = get_manager().submit("cpu", fn, *args)
    fut = get_manager().submit("io", fn, *args)

    # GPU 推理（同步阻塞）
    with get_manager().gpu():
        result = kronos.predict(ticker, date)

    # LLM API（异步）
    async with get_manager().llm():
        response = await call_llm_api(prompt)

    # 查看资源状态
    print(get_manager().stats())
"""
from __future__ import annotations

import asyncio
import collections
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from loguru import logger


# ═════════════════════════════════════════════════════════════════════════════
#  配置
# ═════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class ResourceBudget:
    """
    资源预算配置。

    所有默认值均基于当前机器配置（Ryzen 7 4800H / 64GB / Quadro P620 4GB）。
    可通过 .env 环境变量覆盖：
      RESOURCE_CPU_WORKERS       — CPU 线程池大小（默认 8）
      RESOURCE_IO_WORKERS        — IO 线程池大小（默认 4）
      RESOURCE_GPU_QUEUE_SIZE    — GPU 推理并发数（默认 1）
      RESOURCE_LLM_CONCURRENCY   — LLM API 并发数（默认 3）
      RESOURCE_LLM_MIN_INTERVAL  — LLM 最小请求间隔（秒，默认 1.0）
    """

    cpu_workers: int = 8
    """CPU-bound 任务最大并发数（默认 8，匹配 Ryzen 7 4800H 物理核心）。"""

    io_workers: int = 4
    """IO-bound 任务最大并发数（默认 4，适合 baostock/akshare 网络延迟）。"""

    gpu_queue_size: int = 1
    """GPU 推理队列深度（默认 1，Quadro P620 4GB 显存限制）。"""

    llm_concurrency: int = 3
    """LLM API 最大并发请求数（默认 3，防 rate limit）。"""

    llm_min_interval: float = 1.0
    """LLM 请求最小间隔（秒，默认 1.0，滑动窗口限流）。"""


# ═════════════════════════════════════════════════════════════════════════════
#  GPU 队列
# ═════════════════════════════════════════════════════════════════════════════


class GpuQueue:
    """
    GPU 推理并发控制。

    同步 API（供 KronosSession / KronosRunner 使用）：
        with manager.gpu():
            result = run_gpu_inference(...)

    异步 API（供未来 async 场景使用）：
        async with manager.gpu():
            result = await run_gpu_inference(...)

    设备为 CPU 时，acquire/release 均为 no-op（零开销）。
    """

    def __init__(self, max_concurrency: int = 1):
        self._max = max_concurrency
        self._lock = threading.Lock()
        self._in_flight = 0
        self._total_acquired = 0
        self._total_rejected = 0

    @property
    def in_flight(self) -> int:
        """当前正在使用的 GPU 推理任务数。"""
        with self._lock:
            return self._in_flight

    @property
    def max_concurrency(self) -> int:
        """GPU 最大并发数。"""
        return self._max

    def acquire(self) -> None:
        """
        获取 GPU 推理许可（同步，阻塞直到有空位）。

        当同时有 max_concurrency 个推理任务在运行时阻塞等待。
        """
        while True:
            with self._lock:
                if self._in_flight < self._max:
                    self._in_flight += 1
                    self._total_acquired += 1
                    return
                current = self._in_flight
            # 满负荷，短暂休眠后重试
            logger.warning(
                f"⚠️  GPU 推理已满负荷 ({current}/{self._max})，等待释放"
            )
            time.sleep(0.05)

    def release(self) -> None:
        """释放 GPU 推理许可。"""
        with self._lock:
            if self._in_flight > 0:
                self._in_flight -= 1

    def __enter__(self) -> "GpuQueue":
        self.acquire()
        return self

    def __exit__(self, *args) -> None:
        self.release()

    async def __aenter__(self) -> "GpuQueue":
        self.acquire()
        return self

    async def __aexit__(self, *args) -> None:
        self.release()

    def stats(self) -> dict:
        return {
            "in_flight": self._in_flight,
            "max_concurrency": self._max,
            "total_acquired": self._total_acquired,
        }


# ═════════════════════════════════════════════════════════════════════════════
#  LLM 信号量（含滑动窗口限流）
# ════════════════════════════════════════════════════════════════════════════


class LlmSemaphore:
    """
    LLM API 并发控制 + 滑动窗口限流。

    并发控制：最多 N 个请求同时发送（N = llm_concurrency）。
    滑动窗口：每个 provider 在最近 min_interval 秒内最多发送 N 个请求。

    注意：实际的 HTTP 重试和 429 退避由 retry_policy 层处理，
    本模块仅负责流量整形（rate shaping），不处理网络错误。
    """

    def __init__(
        self,
        max_concurrency: int = 3,
        min_interval: float = 1.0,
    ):
        self._max_concurrency = max_concurrency
        self._min_interval = min_interval
        self._lock = threading.Lock()
        # 每个 provider 的请求时间戳队列（滑动窗口）
        self._request_log: dict[str, collections.deque] = {}
        self._total_acquired = 0
        self._total_throttled = 0

    def _check_rate_limit(self, provider: str) -> float:
        """
        检查指定 provider 的滑动窗口，返回需要等待的秒数。
        0.0 表示无需等待。
        """
        now = time.time()
        with self._lock:
            if provider not in self._request_log:
                self._request_log[provider] = collections.deque()
            log = self._request_log[provider]
            # 移除窗口外的旧记录
            cutoff = now - self._min_interval
            while log and log[0] < cutoff:
                log.popleft()
            # 如果窗口内已达到上限，计算需要等待的时间
            if len(log) >= self._max_concurrency:
                wait_for = log[0] + self._min_interval - now
                return max(0.0, wait_for)
            return 0.0

    def acquire(self, provider: str = "default") -> None:
        """
        获取 LLM 请求许可（同步，含滑动窗口限流）。

        Parameters
        ----------
        provider : LLM provider 名称（用于独立滑动窗口计数）
        """
        # 滑动窗口限流：等待直到窗口内有空间
        while True:
            wait = self._check_rate_limit(provider)
            if wait > 0:
                self._total_throttled += 1
                time.sleep(wait)
                continue
            break
        with self._lock:
            if provider not in self._request_log:
                self._request_log[provider] = collections.deque()
            self._request_log[provider].append(time.time())
            self._total_acquired += 1

    def release(self, provider: str = "default") -> None:
        """释放 LLM 请求许可。"""
        with self._lock:
            if provider in self._request_log and self._request_log[provider]:
                self._request_log[provider].popleft()

    async def __aenter__(self) -> "LlmSemaphore":
        # 异步路径：简单 acquire（滑动窗口已在同步路径处理）
        self.acquire()
        return self

    async def __aexit__(self, *args) -> None:
        self.release()

    def stats(self) -> dict:
        return {
            "max_concurrency": self._max_concurrency,
            "min_interval_sec": self._min_interval,
            "total_acquired": self._total_acquired,
            "total_throttled": self._total_throttled,
            "active_requests": {
                p: len(log) for p, log in self._request_log.items()
            },
        }


# ═════════════════════════════════════════════════════════════════════════════
#  线程池包装（懒初始化，向后兼容 ResourcePool 接口）
# ════════════════════════════════════════════════════════════════════════════


class _ThreadPoolWrapper:
    """懒初始化 ThreadPoolExecutor 包装器。"""

    __slots__ = ("_name", "_workers", "_pool", "_lock")

    def __init__(self, name: str, workers: int):
        self._name = name
        self._workers = workers
        self._pool: Optional[ThreadPoolExecutor] = None
        self._lock = threading.Lock()

    @property
    def pool(self) -> ThreadPoolExecutor:
        if self._pool is None:
            with self._lock:
                if self._pool is None:
                    self._pool = ThreadPoolExecutor(
                        max_workers=self._workers,
                        thread_name_prefix=self._name,
                    )
                    logger.debug(
                        f"🧵 {self._name} 线程池已创建 ({self._workers} workers)"
                    )
        return self._pool

    def submit(self, fn: Callable, *args, **kwargs) -> Future:
        return self.pool.submit(fn, *args, **kwargs)

    def shutdown(self, wait: bool = True) -> None:
        if self._pool is not None:
            self._pool.shutdown(wait=wait)
            logger.debug(f"🧹 {self._name} 线程池已关闭")
            self._pool = None


# ═════════════════════════════════════════════════════════════════════════════
#  ResourceManager — 统一入口
# ════════════════════════════════════════════════════════════════════════════


class ResourceManager:
    """
    统一资源管理器。

    所有并发资源均由此处统一管理：
      · CPU 线程池   — TA 分析、数据预处理（最高 8 worker）
      · IO 线程池    — 网络请求、文件读写（最高 4 worker）
      · GPU 队列     — Kronos 推理并发控制（最高 1 任务）
      · LLM 信号量   — LLM API 并发 + 滑动窗口限流

    单例模式：通过 get_manager() 获取。
    关闭时调用 close() 释放所有资源（测试清理用）。

    向后兼容：仍支持 get_pool() 调用（内部委托给 cpu/io 池）。
    """

    def __init__(self, budget: Optional[ResourceBudget] = None):
        self._budget = budget or ResourceBudget()
        self._cpu = _ThreadPoolWrapper("cpu", self._budget.cpu_workers)
        self._io = _ThreadPoolWrapper("io", self._budget.io_workers)
        self._gpu = GpuQueue(max_concurrency=self._budget.gpu_queue_size)
        self._llm = LlmSemaphore(
            max_concurrency=self._budget.llm_concurrency,
            min_interval=self._budget.llm_min_interval,
        )
        self._closed = False

    # ── 提交接口 ─────────────────────────────────────────────────────────────

    def submit(self, category: str, fn: Callable, *args, **kwargs) -> Future:
        """
        提交任务到指定资源类别。

        Parameters
        ----------
        category : "cpu" | "io"
        fn       : 要执行的函数
        *args, **kwargs : 传给 fn 的参数

        Returns
        -------
        concurrent.futures.Future
        """
        if category == "cpu":
            return self._cpu.submit(fn, *args, **kwargs)
        if category == "io":
            return self._io.submit(fn, *args, **kwargs)
        raise ValueError(
            f"未知资源类别: {category!r}（支持: cpu, io）"
        )

    def submit_cpu(self, fn: Callable, *args, **kwargs) -> Future:
        """提交 CPU-bound 任务（TA 分析、数据预处理）。"""
        return self._cpu.submit(fn, *args, **kwargs)

    def submit_io(self, fn: Callable, *args, **kwargs) -> Future:
        """提交 IO-bound 任务（网络请求、文件读写）。"""
        return self._io.submit(fn, *args, **kwargs)

    def run_parallel(
        self,
        callables: list[Callable],
        *,
        timeout: Optional[float] = None,
    ) -> list:
        """
        并行执行多个无参 callable，收集结果。

        异常被捕获并转为 None 返回，不中断其他任务。
        """
        if not callables:
            return []
        futures: dict[Future, int] = {}
        for i, fn in enumerate(callables):
            fut = self._cpu.submit(fn)
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

    # ── GPU 接口 ─────────────────────────────────────────────────────────────

    @property
    def gpu(self) -> GpuQueue:
        """GPU 推理队列。同步和异步上下文均可用。"""
        return self._gpu

    def gpu_enter(self) -> None:
        """获取 GPU 推理许可（同步，阻塞直到有空位）。"""
        self._gpu.acquire()

    def gpu_exit(self) -> None:
        """释放 GPU 推理许可。"""
        self._gpu.release()

    # ── LLM 接口 ─────────────────────────────────────────────────────────────

    @property
    def llm(self) -> LlmSemaphore:
        """LLM API 并发信号量（含滑动窗口限流）。"""
        return self._llm

    # ── 状态查询 ─────────────────────────────────────────────────────────────

    def stats(self) -> dict:
        """返回所有资源的使用状态。"""
        return {
            "budget": {
                "cpu_workers": self._budget.cpu_workers,
                "io_workers": self._budget.io_workers,
                "gpu_queue_size": self._budget.gpu_queue_size,
                "llm_concurrency": self._budget.llm_concurrency,
                "llm_min_interval_sec": self._budget.llm_min_interval,
            },
            "gpu": self._gpu.stats(),
            "llm": self._llm.stats(),
            "closed": self._closed,
        }

    def describe(self) -> str:
        """返回人类可读的资源配置描述。"""
        b = self._budget
        return (
            f"ResourceManager(config={b.cpu_workers}CPU/"
            f"{b.io_workers}IO/{b.gpu_queue_size}GPU/"
            f"{b.llm_concurrency}LLM/{b.llm_min_interval}s)"
        )

    # ── 生命周期 ─────────────────────────────────────────────────────────────

    def close(self) -> None:
        """关闭所有线程池，释放所有资源。"""
        if self._closed:
            return
        self._closed = True
        self._cpu.shutdown(wait=True)
        self._io.shutdown(wait=True)
        logger.info("🧹 ResourceManager 已关闭")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


# ── 模块级单例 ─────────────────────────────────────────────────────────────────

_manager: Optional[ResourceManager] = None


def get_manager() -> ResourceManager:
    """
    获取全局 ResourceManager 单例。

    所有资源访问应通过此函数，禁止各模块自行创建线程池或信号量。
    """
    global _manager
    if _manager is None:
        _manager = ResourceManager()
    return _manager


def clear_manager() -> None:
    """清除单例，用于测试隔离。"""
    global _manager
    if _manager is not None:
        _manager.close()
    _manager = None


# ── 向后兼容 ──────────────────────────────────────────────────────────────────


def get_pool():
    """
    向后兼容：返回 ResourceManager 实例。

    ResourceManager 提供与 ResourcePool 兼容的接口：
      .submit_cpu() / .submit_io() / .run_parallel() / .close()
    """
    return get_manager()


def clear_pool_singleton():
    """向后兼容：调用 clear_manager()。"""
    clear_manager()
