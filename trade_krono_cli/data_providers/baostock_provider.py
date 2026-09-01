"""
data_providers.baostock_provider — baostock 数据源实现。

封装现有 baostock 调用逻辑，适配 DataProvider 接口。
保留原有的 login/logout、限流、缓存兼容等特性。
"""

from __future__ import annotations

import atexit
import re
import threading
import time
from datetime import datetime
from typing import Optional

from loguru import logger

from trade_krono_cli.config import get_settings
from trade_krono_cli.data_providers.base import (
    DataProvider,
    KlineData,
    RealtimeQuote,
    StockMetadata,
)
from trade_krono_cli.security import TokenBucket, validate_date, validate_ticker

# baostock 模块级状态（保持与 data.py 原有的全局状态兼容）
_bs = None
_HAS_BS = False
_bs_logged_in = False
_bs_limiter: Optional[TokenBucket] = None
_bs_login_lock = threading.Lock()

# ST 标记正则（与 trading_constraints.py 保持一致）
_ST_PATTERNS = re.compile(r"^(ST|\*ST|SST|N ST)", re.IGNORECASE)

# _st_cache: ticker → (is_st: bool, timestamp: float)
# TTL: 30 分钟，避免无限增长
_st_cache: dict[str, tuple[bool, float]] = {}
_ST_CACHE_TTL_SEC = 30 * 60  # 30 分钟


def _cleanup_bs_on_exit() -> None:
    """进程退出时保证 baostock 会话被正确注销。"""
    global _bs, _HAS_BS, _bs_logged_in
    if _bs_logged_in and _bs is not None:
        try:
            _bs.logout()  # type: ignore
        except Exception as e:
            logger.debug(f"baostock logout 失败: {e}")
        _bs_logged_in = False
        _HAS_BS = False
        _bs = None
        logger.debug("baostock 会话已在进程退出时清理")


atexit.register(_cleanup_bs_on_exit)


class BaostockProvider(DataProvider):
    """baostock 数据源实现。"""

    name = "baostock"
    supports_kline = True
    supports_quote = False  # baostock 不提供实时行情，由腾讯 API 替代
    supports_metadata = True

    # ── 内部工具方法 ──────────────────────────────────────────

    def _get_limiter(self) -> TokenBucket:
        global _bs_limiter
        if _bs_limiter is None:
            s = get_settings()
            _bs_limiter = TokenBucket(
                rate=1.0 / s.baostock_sleep_sec,
                capacity=5.0,
            )
        return _bs_limiter

    def _ensure_import(self) -> None:
        global _bs, _HAS_BS
        if _HAS_BS:
            return
        try:
            import baostock as _bs_mod  # type: ignore

            _bs = _bs_mod
            _HAS_BS = True
        except ImportError:
            raise RuntimeError("baostock 未安装，请运行: pip install baostock")

    def _ensure_login(self) -> None:
        global _bs_logged_in
        if not _HAS_BS:
            self._ensure_import()
        if _bs_logged_in:
            return
        with _bs_login_lock:
            if _bs_logged_in:
                return
            lg = _bs.login()  # type: ignore
        if lg.error_code != "0":
            raise RuntimeError(f"baostock 登录失败: {lg.error_msg}")
        _bs_logged_in = True
        logger.debug(f"✅ baostock 登录成功（{self.name} provider）")

    def _logout(self) -> None:
        global _bs_logged_in
        if _bs_logged_in and _bs is not None:
            try:
                _bs.logout()  # type: ignore
            except Exception:
                pass
            _bs_logged_in = False

    def _query_stock_basic(self, ticker: str) -> list[dict]:
        """查询股票基本信息。"""
        self._ensure_login()
        rs = _bs.query_stock_basic(code=ticker)  # type: ignore
        if rs.error_code != "0":
            logger.debug(f"{self.name} 基本查询失败 {ticker}: {rs.error_msg}")
            return []
        rows = []
        while rs.next():
            rows.append(rs.get_row_data())
        return rows

    # ── 核心接口实现 ──────────────────────────────────────────

    def fetch_kline(
        self,
        ticker: str,
        start_date: str,
        end_date: str,
        frequency: str = "d",
        adjustflag: str = "1",
    ) -> Optional[KlineData]:
        ticker = validate_ticker(ticker)
        start_date = validate_date(start_date)
        end_date = validate_date(end_date)

        self._ensure_import()
        self._get_limiter().acquire()
        self._ensure_login()

        try:
            rs = _bs.query_history_k_data_plus(  # type: ignore
                ticker,
                "date,open,high,low,close,volume,amount",
                start_date=start_date,
                end_date=end_date,
                frequency=frequency,
                adjustflag=adjustflag,
            )
        except Exception as e:
            logger.warning(f"{self.name} K 线拉取异常 {ticker}: {str(e)[:200]}")
            return None

        if rs.error_code != "0":
            logger.warning(f"{self.name} K 线查询失败 [{ticker}]: {rs.error_msg}")
            return None

        rows = []
        while rs.next():
            rows.append(rs.get_row_data())

        if not rows:
            logger.debug(f"{self.name} 空数据: {ticker} {start_date}~{end_date}")
            return None

        import pandas as pd

        df = pd.DataFrame(rows, columns=rs.fields)
        df = df.dropna(subset=["close"])

        if df.empty:
            return None

        return KlineData(
            timestamps=pd.to_datetime(df["date"]).tolist(),
            open=df["open"].astype(float).tolist(),
            high=df["high"].astype(float).tolist(),
            low=df["low"].astype(float).tolist(),
            close=df["close"].astype(float).tolist(),
            volume=df["volume"].astype(float).tolist(),
            amount=df["amount"].astype(float).tolist(),
        )

    def fetch_quote(self, ticker: str) -> Optional[RealtimeQuote]:
        """baostock 不提供实时行情，返回 None。"""
        return None

    def fetch_metadata(self, ticker: str) -> Optional[StockMetadata]:
        ticker = validate_ticker(ticker)
        rows = self._query_stock_basic(ticker)
        if not rows:
            return None

        row = rows[0]
        # baostock stock_basic 字段顺序：
        # 0=code, 1=code_name, 2=ipoDate, 3=outDate, ...
        _ = row[0] if len(row) > 0 else ticker  # code: 保留用于调试日志
        name = row[1] if len(row) > 1 else ""
        ipo_date = row[2] if len(row) > 2 else None
        out_date = row[3] if len(row) > 3 else None

        is_st = bool(_ST_PATTERNS.match(name.strip())) if name else False

        return StockMetadata(
            ticker=ticker,
            ipo_date=ipo_date,
            out_date=out_date,
            is_st=is_st,
            source=self.name,
        )

    def check_st_status(self, ticker: str) -> bool:
        """
        检查是否为 ST 标的（带 TTL 缓存，避免无限增长）。
        """
        now = time.time()
        if ticker in _st_cache:
            is_st, cached_at = _st_cache[ticker]
            if now - cached_at < _ST_CACHE_TTL_SEC:
                return is_st
            # 缓存过期，删除旧条目
            del _st_cache[ticker]

        meta = self.fetch_metadata(ticker)
        result = meta.is_st if meta else False
        _st_cache[ticker] = (result, now)
        return result

    def check_delisted(self, ticker: str) -> bool:
        """检查股票是否已退市。"""
        meta = self.fetch_metadata(ticker)
        if not meta or not meta.out_date:
            return False
        try:
            out_dt = datetime.strptime(meta.out_date, "%Y-%m-%d").date()
            return out_dt < datetime.now().date()
        except ValueError:
            return False

    def check_new_stock(
        self, ticker: str, eval_date: str, min_listing_days: int = 60
    ) -> tuple[bool, str]:
        """
        检查是否为次新股。

        Returns
        -------
        (is_new_stock, reason)
        """
        meta = self.fetch_metadata(ticker)
        if not meta or not meta.ipo_date:
            return False, ""
        try:
            ipo_dt = datetime.strptime(meta.ipo_date, "%Y-%m-%d").date()
            eval_dt = datetime.strptime(eval_date, "%Y-%m-%d").date()
            calendar_days = (eval_dt - ipo_dt).days
            trading_days_approx = int(calendar_days * 0.7)
            if trading_days_approx < min_listing_days:
                return True, (
                    f"{ticker}: 次新股，上市约 {trading_days_approx} 个交易日"
                    f" (IPO: {meta.ipo_date})"
                )
        except ValueError:
            pass
        return False, ""

    def health_check(self) -> bool:
        """尝试拉取一只已知股票的基本信息。"""
        try:
            meta = self.fetch_metadata("sh.600519")
            return meta is not None
        except Exception:
            return False
