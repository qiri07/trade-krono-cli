"""
安全工具 — 密钥校验、输入校验、重试、限流。
"""
from __future__ import annotations

import os
import re
import sys
import time
import hashlib
from datetime import datetime
from functools import wraps
from pathlib import Path
from typing import Any, Callable, Optional, TypeVar

from loguru import logger

from trade_krono_cli.config import get_settings

F = TypeVar("F", bound=Callable[..., Any])

# Key-redaction regex — matches API keys and Bearer tokens
_KEY_REDACT_RE = re.compile(r"(sk-[a-zA-Z0-9]{20,}|Bearer\s+[a-zA-Z0-9._\-]+)")


# ═══════════════════════════════════════════════════════
# 输入校验
# ═══════════════════════════════════════════════════════

_TICKER_RE = re.compile(r"^(?:sh\.|sz\.)?([0-9]{6})$")


def validate_ticker(ticker: str) -> str:
    """
    校验并归一化 A 股代码。
    接受: '600519', 'sh.600519', 'SZ.000858'
    返回: 'sh.600519' 或 'sz.000858'
    """
    ticker = ticker.strip().lower()
    m = _TICKER_RE.match(ticker)
    if not m:
        raise ValueError(
            f"无效股票代码: '{ticker}'，应为 6 位数字，如 600519 或 sh.600519"
        )
    code = m.group(1)
    # 判断市场
    if code.startswith(("6", "5", "9")):
        return f"sh.{code}"
    else:
        return f"sz.{code}"


def validate_date(date_str: str) -> str:
    """校验 YYYY-MM-DD 格式。"""
    try:
        dt = datetime.strptime(date_str.strip(), "%Y-%m-%d")
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        raise ValueError(f"无效日期: '{date_str}'，应为 YYYY-MM-DD 格式")


# ═══════════════════════════════════════════════════════
# 重试装饰器
# ═══════════════════════════════════════════════════════

def retry(
    max_attempts: int = 3,
    base_delay: float = 2.0,
    exceptions: tuple[type[Exception], ...] = (Exception,),
) -> Callable[[F], F]:
    """指数退避重试装饰器。"""
    def decorator(fn: F) -> F:
        @wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc: Optional[Exception] = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return fn(*args, **kwargs)
                except exceptions as e:
                    last_exc = e
                    if attempt < max_attempts:
                        delay = base_delay * (2 ** (attempt - 1))
                        logger.warning(
                            f"⚠️  第 {attempt}/{max_attempts} 次尝试失败: {e}，"
                            f"{delay:.1f}s 后重试..."
                        )
                        time.sleep(delay)
            raise last_exc  # type: ignore[misc]
        return wrapper  # type: ignore[return-value]
    return decorator


# ═══════════════════════════════════════════════════════
# 密钥校验
# ═══════════════════════════════════════════════════════

_KEY_ENV_MAP = {
    "deepseek": "DEEPSEEK_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "minimax": "MINIMAX_API_KEY",
    "agnes": "AGNES_API_KEY",
}


class KeyVault:
    """管理 LLM API 密钥的工具类。"""

    def available_providers(self) -> list[str]:
        """返回已配置密钥的供应商列表。"""
        available = []
        for provider, env_key in _KEY_ENV_MAP.items():
            if os.getenv(env_key):
                available.append(provider)
        return available

    def validate(self) -> dict[str, bool]:
        """校验所有密钥，返回状态字典。"""
        result = {}
        for provider, env_key in _KEY_ENV_MAP.items():
            result[provider] = bool(os.getenv(env_key))
        return result

    def get_key(self, provider: str) -> Optional[str]:
        """获取指定供应商的 API key。"""
        env_key = _KEY_ENV_MAP.get(provider)
        if not env_key:
            return None
        return os.getenv(env_key)


# ═══════════════════════════════════════════════════════
# 限流器
# ═══════════════════════════════════════════════════════

class TokenBucket:
    """令牌桶限流器。"""

    def __init__(self, rate: float, capacity: float):
        """
        rate: 每秒生成的令牌数（QPS）
        capacity: 最大令牌数（突发容量）
        """
        self._rate = rate
        self._capacity = capacity
        self._tokens = capacity
        self._last_time = time.monotonic()

    def acquire(self, tokens: float = 1.0) -> None:
        """获取令牌，阻塞直到可用。"""
        now = time.monotonic()
        elapsed = now - self._last_time
        self._last_time = now
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)

        if self._tokens < tokens:
            wait = (tokens - self._tokens) / self._rate
            time.sleep(wait)
            self._tokens = 0.0
        else:
            self._tokens -= tokens


# ═══════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════

def ticker_hash(ticker: str) -> str:
    """为 ticker 生成短哈希，用于缓存键。"""
    return hashlib.md5(ticker.encode()).hexdigest()[:12]


def sanitize_for_log(message: str) -> str:
    """Redact API keys and Bearer tokens from log messages.

    Replaces patterns like ``sk-<20+ chars>`` and ``Bearer <token>`` with
    ``[REDACTED_KEY]`` so secrets never leak into logs or error output.
    """
    return _KEY_REDACT_RE.sub("[REDACTED_KEY]", message)


def ensure_import_path(*paths: Path) -> None:
    """Insert paths into sys.path (harness-first, then root), skipping
    duplicates and non-existent directories.

    Parameters
    ----------
    *paths : Path
        Directories to insert; inserted in order at the front of sys.path.
        Non-existent paths are silently skipped.
    """
    for p in paths:
        sp = str(p)
        if p.exists() and sp not in sys.path:
            sys.path.insert(0, sp)
