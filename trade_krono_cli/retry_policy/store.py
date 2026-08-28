"""
retry_policy.store — 失败记录持久化（FailureStore）。
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from trade_krono_cli.config import get_settings
from trade_krono_cli.retry_policy.classifier import classify_error


@dataclass
class FailureRecord:
    """
    单只股票的失败记录。

    Attributes
    ----------
    ticker          : 股票代码
    date            : 分析日期
    module          : 失败模块（"ta" / "kronos" / "data"）
    error_category  : 错误分类（"retriable" / "non_retriable"）
    error_type      : 异常类型名
    error_message   : 错误消息（脱敏后）
    timestamp       : 记录时间（epoch seconds）
    attempt_count   : 已尝试次数
    """

    ticker: str
    date: str
    module: str
    error_category: str
    error_type: str
    error_message: str
    timestamp: float = field(default_factory=time.time)
    attempt_count: int = 1

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "FailureRecord":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


class FailureStore:
    """
    失败记录持久化存储（JSON 文件）。

    用法：
        store = FailureStore()
        store.record("sh.600519", "2026-01-15", "ta", exc)
        failed = store.list_fails()
        store.clear_for_date("2026-01-15")
    """

    def __init__(self, store_path: Optional[Path] = None) -> None:
        self._path = store_path or (get_settings().cache_dir / "failure_store.json")
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._records: list[FailureRecord] = []
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self._records = [FailureRecord.from_dict(r) for r in data]
        except (json.JSONDecodeError, OSError):
            self._records = []

    def _save(self) -> None:
        with self._lock:
            self._save_unlocked()

    def _save_unlocked(self) -> None:
        """内部写操作，调用方必须已持有锁。"""
        self._path.write_text(
            json.dumps(
                [r.to_dict() for r in self._records],
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def record(
        self,
        ticker: str,
        date: str,
        module: str,
        exc: Exception,
        attempt_count: int = 1,
    ) -> FailureRecord:
        """
        记录一次失败。

        若同一 ticker + date + module 已有记录，则更新 attempt_count 和 error 信息。
        """
        category, desc = classify_error(exc)
        record = FailureRecord(
            ticker=ticker,
            date=date,
            module=module,
            error_category=category,
            error_type=type(exc).__name__,
            error_message=str(exc)[:500],  # 截断防止文件膨胀
            timestamp=time.time(),
            attempt_count=attempt_count,
        )
        with self._lock:
            # 更新已有记录
            for r in self._records:
                if r.ticker == ticker and r.date == date and r.module == module:
                    r.error_category = record.error_category
                    r.error_type = record.error_type
                    r.error_message = record.error_message
                    r.attempt_count = record.attempt_count
                    r.timestamp = record.timestamp
                    self._save_unlocked()
                    return r
            self._records.append(record)
            self._save_unlocked()
            return record

    def list_fails(
        self,
        date: Optional[str] = None,
        module: Optional[str] = None,
        category: Optional[str] = None,
    ) -> list[FailureRecord]:
        """
        查询失败记录。

        Parameters
        ----------
        date     : 筛选日期（None = 全部）
        module   : 筛选模块 "ta"/"kronos"/"data"（None = 全部）
        category : 筛选分类 "retriable"/"non_retriable"（None = 全部）
        """
        result = self._records
        if date:
            result = [r for r in result if r.date == date]
        if module:
            result = [r for r in result if r.module == module]
        if category:
            result = [r for r in result if r.error_category == category]
        return sorted(result, key=lambda r: r.timestamp, reverse=True)

    def get_tickers(
        self,
        date: Optional[str] = None,
        module: Optional[str] = None,
    ) -> list[str]:
        """返回失败股票的 ticker 列表（去重，保留首次出现顺序）。"""
        fails = self.list_fails(date=date, module=module)
        seen: set[str] = set()
        result: list[str] = []
        for r in fails:
            if r.ticker not in seen:
                seen.add(r.ticker)
                result.append(r.ticker)
        return result

    def clear_for_date(self, date: str) -> int:
        """清除指定日期的所有失败记录，返回清除数量。"""
        before = len(self._records)
        self._records = [r for r in self._records if r.date != date]
        cleared = before - len(self._records)
        if cleared:
            self._save()
        return cleared

    def clear_all(self) -> int:
        """清除所有失败记录，返回清除数量。"""
        n = len(self._records)
        self._records = []
        if n:
            self._save()
        return n

    def stats(self) -> dict:
        """返回失败统计。"""
        retriable = sum(1 for r in self._records if r.error_category == "retriable")
        non_retriable = sum(1 for r in self._records if r.error_category == "non_retriable")
        by_module: dict[str, int] = {}
        for r in self._records:
            by_module[r.module] = by_module.get(r.module, 0) + 1
        return {
            "total": len(self._records),
            "retriable": retriable,
            "non_retriable": non_retriable,
            "by_module": by_module,
        }
