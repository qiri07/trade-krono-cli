"""data_providers.akshare_provider — AkShare 数据源实现。

AkShare 是免费开源的 A 股数据接口，无需注册 key。
覆盖范围：K 线、实时行情、部分基本面数据。

API 参考：
  - K 线: ak.stock_zh_a_hist(symbol, start_date, end_date, adjust)
  - 实时行情: ak.stock_zh_a_spot_em()
  - 行业分类: ak.stock_board_industry_name_em()
"""

from __future__ import annotations

import re
import time
from typing import Any

from loguru import logger

from trade_krono_cli.data_providers.base import (
    DataProvider,
    KlineData,
    RealtimeQuote,
    StockMetadata,
)
from trade_krono_cli.utils import pd_to_datetime_safe, safe_float

_ST_PATTERNS = re.compile(r"^(ST|\*ST|SST|N ST)", re.IGNORECASE)

# 全市场实时行情缓存（TTL 60 秒），避免每次 fetch_quote 都重新拉取全市场
_full_market_cache: dict[str, Any] = {"data": None, "timestamp": 0.0}
_FULL_MARKET_CACHE_TTL_SEC = 60


class AkShareProvider(DataProvider):
    """AkShare 数据源实现。"""

    name = "akshare"
    supports_kline = True
    supports_quote = True
    supports_metadata = False  # akshare 不提供完整的基本面元数据

    # ── 懒加载 ────────────────────────────────────────────────

    _ak: Any = None

    @classmethod
    def _ensure_import(cls) -> None:
        if cls._ak is not None:
            return
        try:
            import akshare as ak  # type: ignore

            cls._ak = ak
        except ImportError:
            msg = "akshare 未安装，无法使用 akshare 数据源。请运行: pip install akshare"
            raise RuntimeError(
                msg,
            )

    @classmethod
    def _get_full_market_cache(cls) -> Any:
        """获取全市场实时行情缓存，TTL 内直接返回，过期则重新拉取。"""
        now = time.time()
        cached = _full_market_cache
        if cached["data"] is not None and (now - cached["timestamp"]) < _FULL_MARKET_CACHE_TTL_SEC:
            return cached["data"]
        try:
            df = cls._ak.stock_zh_a_spot_em()
            if df is not None and not df.empty:
                cached["data"] = df
                cached["timestamp"] = now
            return cached["data"]
        except Exception as e:
            logger.debug(f"akshare 全市场行情拉取失败: {e}")
            return None

    # ── 内部转换工具 ──────────────────────────────────────────

    @staticmethod
    def _ticker_to_ak(ticker: str) -> str:
        """将 sh.600519 格式转换为 akshare 的 600519 格式。"""
        return ticker.rsplit(".", maxsplit=1)[-1]

    @staticmethod
    def _ak_to_ticker(code: str) -> str:
        """将 600519 格式转换为 sh.600519 格式。"""
        if code.startswith(("6", "5", "9")):
            return f"sh.{code}"
        return f"sz.{code}"

    # ── 核心接口实现 ──────────────────────────────────────────

    def fetch_kline(
        self,
        ticker: str,
        start_date: str,
        end_date: str,
        frequency: str = "d",
        adjustflag: str = "1",
    ) -> KlineData | None:
        if frequency != "d":
            logger.debug(f"{self.name} 暂不支持频率 {frequency}，跳过")
            return None

        try:
            self._ensure_import()
            code = self._ticker_to_ak(ticker)
            # akshare 日期格式: 20250101
            start_fmt = start_date.replace("-", "")
            end_fmt = end_date.replace("-", "")

            df = self._ak.stock_zh_a_hist(
                symbol=code,
                start_date=start_fmt,
                end_date=end_fmt,
                adjust=adjustflag,
            )
            if df is None or df.empty:
                return None

            return KlineData(
                timestamps=pd_to_datetime_safe(df["日期"].tolist()),
                open=df["开盘"].astype(float).tolist(),
                high=df["最高"].astype(float).tolist(),
                low=df["最低"].astype(float).tolist(),
                close=df["收盘"].astype(float).tolist(),
                volume=df["成交量"].astype(float).tolist(),
                amount=df["成交额"].astype(float).tolist(),
            )
        except ImportError:
            raise
        except Exception as e:
            logger.warning(f"{self.name} K 线拉取异常 {ticker}: {str(e)[:200]}")
            return None

    def fetch_quote(self, ticker: str) -> RealtimeQuote | None:
        try:
            self._ensure_import()
            # 使用缓存的全市场实时行情，避免重复拉取
            df = self._get_full_market_cache()
            if df is None or df.empty:
                return None

            code = self._ticker_to_ak(ticker)
            row = df[df["代码"] == code]
            if row.empty:
                return None

            r = row.iloc[0]
            return RealtimeQuote(
                ticker=ticker,
                price=safe_float(r.get("最新价")),
                pe=safe_float(r.get("市盈率-动态")),
                pb=safe_float(r.get("市净率")),
                market_cap=safe_float(r.get("总市值")),
                turnover=safe_float(r.get("换手率")),
                source=self.name,
            )
        except ImportError:
            raise
        except Exception as e:
            logger.debug(f"{self.name} 实时行情异常 {ticker}: {str(e)[:100]}")
            return None

    def fetch_metadata(self, ticker: str) -> StockMetadata | None:
        """Akshare 不提供完整的 ST/退市/行业元数据，返回 None。"""
        return None

    def health_check(self) -> bool:
        try:
            self._ensure_import()
            # 简单检查：尝试拉取一只股票的 K 线（短时间范围）
            df = self._ak.stock_zh_a_hist(
                symbol="600519",
                start_date="20260801",
                end_date="20260813",
                adjust="1",
            )
            return df is not None and not df.empty
        except Exception as e:
            logger.debug(f"akshare health check 异常: {e}")
            return False
