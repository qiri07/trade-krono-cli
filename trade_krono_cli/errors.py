"""
错误隔离 — ModuleError 及各模块的失败封装。

让单个模块（Kronos / TA / Risk）的失败不影响整个 pipeline 继续运行。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, TypeVar

T = TypeVar("T")


class ModuleError(Exception):
    """
    单个 pipeline 模块执行失败的封装异常。

    区别：
      - 普通 Exception : 表示不可恢复的错误，应中断流程
      - ModuleError   : 表示某个子模块失败，其他模块可以继续运行
    """

    def __init__(
        self,
        module: str,
        message: str,
        original_exception: Optional[Exception] = None,
        context: Optional[dict[str, Any]] = None,
    ):
        self.module = module
        self.message = message
        self.original_exception = original_exception
        self.context = context or {}
        super().__init__(f"[{module}] {message}")

    def to_dict(self) -> dict:
        return {
            "module": self.module,
            "message": str(self),
            "original": (
                f"{type(self.original_exception).__name__}: {self.original_exception}"
                if self.original_exception else None
            ),
            "context": self.context,
        }


@dataclass
class ModuleResult:
    """
    模块执行结果的统一包装。

    用法：
        result = ModuleResult(success=True, data=..., error=None)
        result = ModuleResult(success=False, data=None, error=ModuleError(...))
    """

    success: bool
    data: Optional[Any] = None
    error: Optional[ModuleError] = None
    elapsed_sec: float = 0.0

    def is_ok(self) -> bool:
        return self.success and self.error is None

    def to_dict(self) -> dict:
        d = {
            "success": self.success,
            "elapsed_sec": self.elapsed_sec,
        }
        if self.error:
            d["error"] = self.error.to_dict()
        if self.data is not None:
            d["data_summary"] = _summarize(self.data)
        return d


def _summarize(data: Any) -> Any:
    """将数据转换为可序列化的摘要（避免大对象）。"""
    if isinstance(data, list):
        return f"[{len(data)} items]"
    if isinstance(data, dict):
        return {k: _summarize(v) for k, v in list(data.items())[:5]}
    return data


def safe_run(
    fn,
    *args,
    module: str = "unknown",
    **kwargs,
) -> ModuleResult:
    """
    安全执行函数，失败时封装为 ModuleResult(success=False)。

    Parameters
    ----------
    fn : 要执行的函数
    *args, **kwargs : 传递给 fn 的参数
    module : 模块名称（用于错误标识）

    Returns
    -------
    ModuleResult
    """
    import time
    t0 = time.time()
    try:
        result = fn(*args, **kwargs)
        return ModuleResult(success=True, data=result, elapsed_sec=time.time() - t0)
    except Exception as e:
        err = ModuleError(
            module=module,
            message=str(e),
            original_exception=e,
            context={"args": str(args)[:200], "kwargs": str(kwargs)[:200]},
        )
        return ModuleResult(success=False, error=err, elapsed_sec=time.time() - t0)
