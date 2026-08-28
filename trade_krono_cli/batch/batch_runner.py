"""
batch_runner — 动态 batch 调度器。

支持 asyncio 并发执行，动态调整 batch_size，
内置限流防止 API rate limit。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Coroutine, Optional

from loguru import logger


@dataclass
class BatchConfig:
    """批次调度配置。"""

    batch_size: int = 5
    """每批处理的最大股票数。"""

    max_concurrent: int = 3
    """最大并发数（异步任务数）。"""

    cooldown_seconds: float = 2.0
    """批次间冷却时间（防 rate limit）。"""

    retry_attempts: int = 2
    """单个任务失败后的重试次数。"""

    retry_delay: float = 1.0
    """重试等待时间（秒）。"""


@dataclass
class BatchResult:
    """单个批次的执行结果。"""

    success: bool
    data: list[Any] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    elapsed_sec: float = 0.0


class BatchRunner:
    """
    动态 batch 调度器。

    用法：
        runner = BatchRunner(config)
        results = await runner.run(tasks, process_fn)
    """

    def __init__(self, config: Optional[BatchConfig] = None):
        self.config = config or BatchConfig()
        self._semaphore: Optional[asyncio.Semaphore] = None

    async def run(
        self,
        items: list[Any],
        process_fn: Callable[[Any], Coroutine[Any, Any, Any]],
    ) -> list[Any]:
        """
        批量执行异步任务，动态调整 concurrency。

        Parameters
        ----------
        items : 待处理项列表
        process_fn : 异步处理函数 fn(item) -> result

        Returns
        -------
        成功结果列表（失败项记录 error 但不中断）
        """
        if not items:
            return []

        self._semaphore = asyncio.Semaphore(self.config.max_concurrent)
        results: list[Any] = []
        errors: list[str] = []

        async def _run_with_semaphore(item: Any, idx: int) -> tuple[Any | None, str | None]:
            assert self._semaphore is not None
            async with self._semaphore:
                for attempt in range(1 + self.config.retry_attempts):
                    try:
                        t0 = time.time()
                        result = await process_fn(item)
                        elapsed = time.time() - t0
                        logger.debug(f"  ✓ [{idx}] 完成 ({elapsed:.1f}s)")
                        return result, None
                    except Exception as e:
                        if attempt < self.config.retry_attempts:
                            logger.warning(
                                f"  ⚠ [{idx}] 第 {attempt + 1} 次失败: {e}，"
                                f"等待 {self.config.retry_delay}s 后重试"
                            )
                            await asyncio.sleep(self.config.retry_delay)
                        else:
                            logger.error(f"  ✗ [{idx}] 最终失败: {e}")
                            return None, f"{type(e).__name__}: {e}"
                return None, None  # unreachable but satisfies mypy

        # 分批处理
        batches = self._split_batches(items, self.config.batch_size)
        total_batches = len(batches)
        logger.info(
            f"📦 BatchRunner 启动 | {len(items)} 项 / "
            f"batch_size={self.config.batch_size} / "
            f"concurrent={self.config.max_concurrent}"
        )

        for batch_idx, batch in enumerate(batches):
            batch_start = time.time()
            batch_results: list[tuple[Any | None, str | None]] = await asyncio.gather(
                *[_run_with_semaphore(item, i) for i, item in enumerate(batch)],
                return_exceptions=False,
            )

            for result, error in batch_results:
                if result is not None:
                    results.append(result)
                if error is not None:
                    errors.append(error)

            # 批次间冷却
            if batch_idx < total_batches - 1 and self.config.cooldown_seconds > 0:
                await asyncio.sleep(self.config.cooldown_seconds)

            batch_elapsed = time.time() - batch_start
            logger.info(
                f"  📊 批次 {batch_idx + 1}/{total_batches} 完成 "
                f"({batch_elapsed:.1f}s, 成功 {sum(1 for r, e in batch_results if r is not None)}/"
                f"{len(batch)})"
            )

        logger.info(f"📊 BatchRunner 完成 | 成功 {len(results)}/{len(items)}, 失败 {len(errors)}")
        return results

    def run_sync(
        self,
        items: list[Any],
        process_fn: Callable[[Any], Any],
    ) -> list[Any]:
        """
        同步版本：包装 asyncio.run。
        """

        async def _async_runner():
            async def _wrap(item):
                return process_fn(item)

            return await self.run(items, _wrap)

        return asyncio.run(_async_runner())

    @staticmethod
    def _split_batches(items: list[Any], size: int) -> list[list[Any]]:
        """将列表分割为固定大小的批次。"""
        return [items[i : i + size] for i in range(0, len(items), size)]
