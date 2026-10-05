"""data_providers.akshare_hk_provider — AkShare 港股数据源实现。

使用 akshare 免费港股接口 `stock_hk_daily()` 拉取历史 K 线数据。
无需注册 key，覆盖港股全量历史行情。

API:
  - K 线: ak.stock_hk_daily(symbol, adjust='qfq')
  - 返回列: date, open, high, low, close, volume, amount
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from loguru import logger

from trade_krono_cli.data_providers.base import (
    DataProvider,
    KlineData,
    RealtimeQuote,
    StockMetadata,
)
from trade_krono_cli.utils import pd_to_datetime_safe

if TYPE_CHECKING:
    pass  # No type-only imports needed


class AkShareHKProvider(DataProvider):
    """AkShare 港股数据源实现。"""

    name = "akshare_hk"
    supports_kline = True
    supports_quote = False
    supports_metadata = False

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
            msg = "akshare 未安装，无法使用 akshare_hk 数据源。请运行: uv add akshare"
            raise RuntimeError(msg)

    # ── 工具方法 ────────────────────────────────────────────────

    @staticmethod
    def _ticker_to_symbol(ticker: str) -> str:
        """将 hk.00700 格式转换为 akshare 的 6 位纯数字代码。"""
        return ticker.rsplit(".", maxsplit=1)[-1]

    # ── 核心接口实现 ──────────────────────────────────────────

    def fetch_kline(
        self,
        ticker: str,
        start_date: str | None = None,
        end_date: str | None = None,
        frequency: str = "d",
        adjustflag: str = "1",
    ) -> KlineData | None:
        """拉取港股日线 K 线数据。

        akshare stock_hk_daily 一次性返回全量历史，调用后在内存中按日期过滤。

        Parameters
        ----------
        ticker : str
            港股 ticker（格式：hk.00700）
        start_date : str | None
            起始日期 YYYY-MM-DD，为空时不限定下限
        end_date : str | None
            结束日期 YYYY-MM-DD，为空时不限定上限
        frequency : str
            频率，默认 "d"（日线）
        adjustflag : str
            复权方式，"0"=不复权，"1"=前复权，"2"=后复权

        Returns
        -------
        KlineData | None
            标准化 K 线数据，拉取失败时返回 None
        """
        if frequency != "d":
            logger.debug(f"{self.name} 暂不支持频率 {frequency}，跳过")
            return None

        try:
            self._ensure_import()
            symbol = self._ticker_to_symbol(ticker)

            df = self._ak.stock_hk_daily(symbol=symbol, adjust=adjustflag)
            if df is None or df.empty:
                logger.debug(f"{self.name} 无数据: {ticker}")
                return None

            # 内存中按日期过滤
            if start_date is not None:
                df = df[df["date"] >= start_date]
            if end_date is not None:
                df = df[df["date"] <= end_date]

            if df.empty:
                return None

            # 列名映射：akshare 返回 date/open/high/low/close/volume/amount
            timestamps = pd_to_datetime_safe(df["date"].tolist())
            return KlineData(
                timestamps=timestamps,
                open=df["open"].astype(float).tolist(),
                high=df["high"].astype(float).tolist(),
                low=df["low"].astype(float).tolist(),
                close=df["close"].astype(float).tolist(),
                volume=df["volume"].astype(float).tolist(),
                amount=df["amount"].astype(float).tolist(),
            )
        except ImportError:
            raise
        except Exception as e:
            logger.warning(f"{self.name} K 线拉取异常 {ticker}: {str(e)[:200]}")
            return None

    def health_check(self) -> bool:
        """检查港股数据源是否可用。"""
        try:
            self._ensure_import()
            # 轻量测试：拉取腾讯控股最新一条数据
            df = self._ak.stock_hk_daily(symbol="00700", adjust="qfq")  # type: ignore[attr-defined]
            return df is not None and not df.empty  # type: ignore[union-attr]
        except Exception as e:
            logger.debug(f"{self.name} 健康检查失败: {e}")
            return False

    def fetch_quote(self, ticker: str) -> "RealtimeQuote | None":  # type: ignore[override]
        """港股 Provider 不支持实时行情。"""
        return None

    def fetch_metadata(self, ticker: str) -> "StockMetadata | None":  # type: ignore[override]
        """港股 Provider 不提供元数据。"""
        return None
