"""baostock 会话管理 — 惰性导入、登录、限速。

从 data.py 拆分出来，专门管理 baostock 的模块级状态：
  - _bs             baostock 模块引用
  - _HAS_BS         baostock 是否已导入
  - _bs_logged_in   baostock 是否已登录
  - _bs_limiter     速率限制器（TokenBucket）
"""

from __future__ import annotations

import threading

from loguru import logger

from trade_krono_cli.config import Settings, get_settings
from trade_krono_cli.security import TokenBucket

# baostock 惰性导入
_bs = None
_HAS_BS = False
_bs_logged_in = False
_bs_limiter: TokenBucket | None = None
_bs_login_lock = threading.Lock()


def _get_limiter(settings: Settings | None = None) -> TokenBucket:
    """获取（或创建）baostock 速率限制器。"""
    global _bs_limiter
    if _bs_limiter is None:
        s = settings or get_settings()
        _bs_limiter = TokenBucket(
            rate=1.0 / s.baostock_sleep_sec,
            capacity=5.0,
        )
    return _bs_limiter


def clear_baostock_globals() -> None:
    """重置 baostock 模块级状态，用于测试隔离。

    被清除的状态：
      - _bs             baostock 模块引用
      - _HAS_BS         baostock 是否已导入
      - _bs_logged_in   baostock 是否已登录
      - _bs_limiter     速率限制器
    """
    global _bs, _HAS_BS, _bs_logged_in, _bs_limiter
    _bs = None
    _HAS_BS = False
    _bs_logged_in = False
    _bs_limiter = None


def _ensure_bs_import() -> None:
    """确保 baostock 模块已被导入。"""
    global _bs, _HAS_BS
    if _HAS_BS:
        return
    try:
        import baostock as _bs_mod  # type: ignore

        _bs = _bs_mod
        _HAS_BS = True
    except ImportError:
        msg = "baostock 未安装，无法拉取 K 线。请运行: pip install baostock"
        raise RuntimeError(msg)


def _ensure_bs_login() -> None:
    """确保 baostock 已登录（线程安全，双重检查锁定）。"""
    global _bs_logged_in
    if not _HAS_BS:
        _ensure_bs_import()
    if _bs_logged_in:
        return
    with _bs_login_lock:
        # 双重检查：另一线程可能已在此等待锁期间完成登录
        if _bs_logged_in:
            return
        lg = _bs.login()  # type: ignore
    if lg.error_code != "0":
        msg = f"baostock 登录失败: {lg.error_msg}"
        raise RuntimeError(msg)
    _bs_logged_in = True
    logger.info("✅ baostock 登录成功")
