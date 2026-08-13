"""
错误分级与差异化重试 — 智能退避策略。

职责：
  · 错误分类：可重试（网络超时、限流、5xx）vs 不可重试（参数/数据/鉴权）
  · 智能重试：根据错误类型决定退避策略（指数 / 自适应 / 立即放弃）
  · 失败持久化：记录每只股票的失败原因，支持单独重跑失败项

设计原则：
  · 网络波动 → 指数退避 + 抖动，最多 N 次
  · 限流（429）→ 解析 Retry-After 头，自适应等待
  · 参数/数据缺失 → 直接放弃，不浪费重试
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Optional, TypeVar, Union

from loguru import logger

from trade_krono_cli.config import get_settings

F = TypeVar("F", bound=Callable[..., Any])


# ═══════════════════════════════════════════════════════
# 错误分类层次
# ═══════════════════════════════════════════════════════

class TradeKronoRetryableError(Exception):
    """可重试错误的基类：网络超时、限流、服务端 5xx 等瞬时故障。"""


class TradeKronoNonRetryableError(Exception):
    """不可重试错误的基类：参数错误、数据不存在、鉴权失败等永久故障。"""


# ── 可重试子类 ────────────────────────────────────────────────────────────────

class NetworkError(TradeKronoRetryableError):
    """网络连接/超时错误。"""


class TimeoutError(TradeKronoRetryableError):
    """请求超时。"""


class RateLimitError(TradeKronoRetryableError):
    """限流错误（HTTP 429 / LLM rate limit）。

    Attributes
    ----------
    retry_after : float | None
        服务端建议的等待秒数（来自 Retry-After 头），None 表示未提供。
    """

    def __init__(
        self,
        message: str,
        retry_after: Optional[float] = None,
        response_headers: Optional[dict[str, str]] = None,
    ) -> None:
        super().__init__(message)
        self.retry_after = retry_after
        self.response_headers = response_headers or {}


class Server5xxError(TradeKronoRetryableError):
    """服务端 5xx 错误（非客户端责任）。"""

    def __init__(self, message: str, status_code: Optional[int] = None) -> None:
        super().__init__(message)
        self.status_code = status_code


# ── 不可重试子类 ──────────────────────────────────────────────────────────────

class ParameterError(TradeKronoNonRetryableError):
    """参数校验失败。"""


class DataNotFoundError(TradeKronoNonRetryableError):
    """数据不存在（如股票退市、停牌、K 线为空）。"""


class AuthError(TradeKronoNonRetryableError):
    """鉴权失败（密钥无效、token 过期）。"""


class ValidationError(TradeKronoNonRetryableError):
    """数据校验失败（格式异常、数据过旧）。"""


# ═══════════════════════════════════════════════════════
# 错误分类器
# ═══════════════════════════════════════════════════════

def classify_error(exc: Exception) -> tuple[str, str]:
    """
    将异常分类为 (category, description)。

    Parameters
    ----------
    exc : Exception
        待分类的异常。

    Returns
    -------
    (category, description)
      category   : "retriable" | "non_retriable"
      description: 人类可读的错误描述
    """
    # 已经是明确的分类错误
    if isinstance(exc, TradeKronoRetryableError):
        return ("retriable", str(exc))
    if isinstance(exc, TradeKronoNonRetryableError):
        return ("non_retriable", str(exc))

    # 基于异常类型推断
    exc_type = type(exc).__name__
    msg = str(exc).lower()

    # 参数错误
    if any(kw in msg for kw in ("invalid", "illegal", "bad parameter", "missing required")):
        return ("non_retriable", f"参数错误: {exc}")

    # 数据不存在
    if any(kw in msg for kw in ("not found", "no data", "empty data", "data not found",
                                 "数据不足", "空数据", "数据不存在", "数据为空")):
        return ("non_retriable", f"数据缺失: {exc}")

    # 鉴权失败
    if any(kw in msg for kw in ("auth", "unauthorized", "forbidden", "invalid api key",
                                 "鉴权", "认证失败", "权限不足", "密钥无效",
                                 "401", "403")):
        return ("non_retriable", f"鉴权失败: {exc}")

    # 限流
    if any(kw in msg for kw in ("rate limit", "too many request", "429",
                                 "限流", "请求过于频繁")):
        return ("retriable", f"限流: {exc}")

    # 服务端 5xx
    if re.search(r"\b5\d\d\b", msg) or any(kw in msg for kw in ("500", "502", "503", "504")):
        return ("retriable", f"服务端错误: {exc}")

    # 网络/超时
    if exc_type in ("ConnectionError", "TimeoutError", "ConnectTimeout",
                    "ReadTimeout", "URLError", "OSError") or \
       any(kw in msg for kw in ("connection", "timeout", "network", "网络", "超时")):
        return ("retriable", f"网络错误: {exc}")

    # 数据验证失败
    if any(kw in msg for kw in ("invalid date", "data validation", "format",
                                 "数据格式", "数据过旧", "未来数据")):
        return ("non_retriable", f"数据验证失败: {exc}")

    # 默认：未知错误，保守处理为不可重试（避免无限重试消耗资源）
    return ("non_retriable", f"未知错误: {exc}")


# ═══════════════════════════════════════════════════════
# 重试策略配置
# ═══════════════════════════════════════════════════════

@dataclass
class RetryPolicy:
    """
    重试策略配置。

    Attributes
    ----------
    max_attempts       : 最大尝试次数（含首次），仅对 retriable 错误生效
    base_delay         : 初始退避秒数
    jitter             : 是否添加随机抖动（0.0–1.0），防止 thundering herd
    rate_limit_backoff : 限流时是否启用自适应退避（解析 Retry-After 头）
    rate_limit_max_wait: 限流自适应退避上限（秒）
    skip_non_retriable : 是否跳过不可重试错误（默认 True，非 retriable 直接抛）
    """

    max_attempts: int = 3
    base_delay: float = 2.0
    jitter: bool = True
    rate_limit_backoff: bool = True
    rate_limit_max_wait: float = 60.0
    skip_non_retriable: bool = True

    # 各错误类型的覆盖配置（可选）
    network_attempts: Optional[int] = None
    network_delay: Optional[float] = None
    rate_limit_attempts: Optional[int] = None
    rate_limit_delay: Optional[float] = None


# ═══════════════════════════════════════════════════════
# 智能重试装饰器
# ═══════════════════════════════════════════════════════

def smart_retry(
    policy: Optional[Union[RetryPolicy, F]] = None,
) -> Union[Callable[[F], F], F]:
    """
    智能重试装饰器：根据错误类型差异化退避。

    支持两种用法：
      @smart_retry                     # 使用默认策略
      @smart_retry(RetryPolicy(...))    # 使用指定策略

    行为：
      · retriable 错误：指数退避（+ 可选抖动），最多 max_attempts 次
      · rate limit 错误：若 enable_rate_limit_backoff=True，
        优先使用 Retry-After 头，否则回退到指数退避
      · non_retriable 错误：直接抛出（skip_non_retriable=True 时）
    """
    # 处理 @smart_retry（无括号）的情况：第一个参数是函数本身
    if callable(policy) and not isinstance(policy, RetryPolicy):
        fn = policy  # type: ignore[assignment]
        policy = RetryPolicy()
        return _make_smart_retry_decorator(fn, policy)

    actual_policy: RetryPolicy = policy if policy is not None else RetryPolicy()

    def decorator(fn: F) -> F:
        return _make_smart_retry_decorator(fn, actual_policy)

    return decorator


def _make_smart_retry_decorator(fn: F, policy: RetryPolicy) -> F:
    """内部：创建带有指定策略的重试装饰器。"""
    @wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        last_exc: Optional[Exception] = None

        for attempt in range(1, policy.max_attempts + 1):
            try:
                return fn(*args, **kwargs)
            except TradeKronoNonRetryableError as e:
                logger.warning(
                    f"❌ [{fn.__name__}] 不可重试错误（放弃）: {e}"
                )
                raise

            except Exception as e:
                last_exc = e
                category, desc = classify_error(e)

                if category == "non_retriable" and policy.skip_non_retriable:
                    logger.warning(
                        f"❌ [{fn.__name__}] 不可重试错误（放弃）: {desc}"
                    )
                    raise

                if attempt >= policy.max_attempts:
                    logger.error(
                        f"❌ [{fn.__name__}] "
                        f"第 {attempt}/{policy.max_attempts} 次尝试仍失败 [{category}]: {desc}"
                    )
                    break

                delay = _compute_delay(e, attempt, policy)
                logger.warning(
                    f"⚠️  [{fn.__name__}] "
                    f"第 {attempt}/{policy.max_attempts} 次失败 [{category}]，"
                    f"{delay:.1f}s 后重试... ({desc[:80]})"
                )
                time.sleep(delay)

        raise last_exc  # type: ignore[misc]

    return wrapper  # type: ignore[return-value]


def _compute_delay(exc: Exception, attempt: int, policy: RetryPolicy) -> float:
    """
    根据异常类型和策略计算退避时间。

    - RateLimitError：优先使用 Retry-After 头（若配置了自适应退避）
    - 其他 retriable：指数退避 + 可选抖动
    """
    # 限流自适应退避
    if isinstance(exc, RateLimitError) and policy.rate_limit_backoff:
        if exc.retry_after is not None and exc.retry_after > 0:
            wait = min(float(exc.retry_after), policy.rate_limit_max_wait)
            logger.debug(
                f"🔄 限流退避：Retry-After={exc.retry_after}s，等待 {wait:.1f}s"
            )
            return wait
        # 无 Retry-After 头时回退到指数退避
        return _exp_backoff(attempt, policy.rate_limit_delay or policy.base_delay, policy.jitter)

    # 普通网络/服务端错误：指数退背
    return _exp_backoff(attempt, policy.base_delay, policy.jitter)


def _exp_backoff(attempt: int, base_delay: float, jitter: bool) -> float:
    """指数退避 + 可选抖动。"""
    delay = base_delay * (2 ** (attempt - 1))
    if jitter:
        delay = delay * (0.5 + 0.5 * time.random())  # [0.5, 1.0] 随机因子
    return delay


# ═══════════════════════════════════════════════════════
# 失败记录与持久化
# ═══════════════════════════════════════════════════════

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
        self._path = store_path or (
            get_settings().cache_dir / "failure_store.json"
        )
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._records: list[FailureRecord] = []
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
        self._path.write_text(
            json.dumps(
                [r.to_dict() for r in self._records],
                ensure_ascii=False, indent=2,
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
        # 更新已有记录
        for r in self._records:
            if r.ticker == ticker and r.date == date and r.module == module:
                r.error_category = record.error_category
                r.error_type = record.error_type
                r.error_message = record.error_message
                r.attempt_count = record.attempt_count
                r.timestamp = record.timestamp
                self._save()
                return r
        self._records.append(record)
        self._save()
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


# ═══════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════

def parse_retry_after(header_value: Optional[str]) -> Optional[float]:
    """
    解析 HTTP Retry-After 响应头。

    支持两种格式：
      - 整数秒数："120"
      - HTTP 日期字符串："Wed, 21 Oct 2025 07:28:00 GMT"

    Returns
    -------
    float | None
        等待秒数，无法解析时返回 None。
    """
    if not header_value:
        return None
    header_value = header_value.strip()

    # 格式 1：秒数
    try:
        seconds = float(header_value)
        if seconds >= 0:
            return seconds
    except ValueError:
        pass

    # 格式 2：HTTP 日期
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(header_value)
        now = time.time()
        wait = dt.timestamp() - now
        return max(wait, 0.0)
    except (ValueError, OSError):
        pass

    return None


def make_rate_limit_error(
    message: str,
    headers: Optional[dict[str, str]] = None,
) -> RateLimitError:
    """
    从 HTTP 响应构建 RateLimitError，自动提取 Retry-After 头。
    """
    retry_after = None
    if headers:
        for key in ("Retry-After", "retry-after", "X-RateLimit-Reset"):
            val = headers.get(key)
            if val:
                retry_after = parse_retry_after(val)
                if retry_after is not None:
                    break
    return RateLimitError(message=message, retry_after=retry_after, response_headers=headers or {})


def make_network_error(message: str) -> NetworkError:
    return NetworkError(message)


def make_timeout_error(message: str) -> TimeoutError:
    return TimeoutError(message)


def make_data_not_found(message: str) -> DataNotFoundError:
    return DataNotFoundError(message)


def make_auth_error(message: str) -> AuthError:
    return AuthError(message)


def make_parameter_error(message: str) -> ParameterError:
    return ParameterError(message)


# ═══════════════════════════════════════════════════════
# 模块级单例
# ═══════════════════════════════════════════════════════

_failure_store: Optional[FailureStore] = None


def get_failure_store() -> FailureStore:
    global _failure_store
    if _failure_store is None:
        _failure_store = FailureStore()
    return _failure_store


def clear_failure_store_singleton() -> None:
    """清除单例，用于测试隔离。"""
    global _failure_store
    _failure_store = None
