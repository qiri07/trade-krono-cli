"""data_providers.mootdx_provider — MootDx 数据源实现。

MootDx 是开源的行情数据接口，支持 level-2 数据。
无需注册，但需要安装 mootdx 包。

API 参考：
  - K 线: MdxApi.factory().get_security_bars()
  - 实时行情: MdxApi.factory().get_security_quotes()
"""

from __future__ import annotations

from typing import Any

from loguru import logger

from trade_krono_cli.data_providers.base import (
    DataProvider,
    KlineData,
    RealtimeQuote,
    StockMetadata,
)
from trade_krono_cli.utils import pd_to_datetime_safe, safe_float


class MootDxProvider(DataProvider):
    """MootDx 数据源实现。"""

    name = "mootdx"
    supports_kline = True
    supports_quote = True
    supports_metadata = False

    # ── 懒加载 ────────────────────────────────────────────────

    _client: Any = None
    _connected: bool = False

    @classmethod
    def _ensure_client(cls) -> None:
        if cls._client is not None and cls._connected:
            return
        try:
            from mootdx.quotes import Quotes  # type: ignore

            cls._client = Quotes.factory(market="std")
            cls._connected = True
        except ImportError:
            msg = "mootdx 未安装，无法使用 mootdx 数据源。请运行: pip install mootdx"
            raise RuntimeError(msg)
        except Exception as e:
            # 连接失败时重置状态，下次调用会重新建立
            logger.debug(f"mootdx 连接失败: {e}，已重置客户端状态")
            cls._client = None
            cls._connected = False
            raise

    # ── 内部转换工具 ──────────────────────────────────────────

    @staticmethod
    def _ticker_to_mootdx(ticker: str) -> tuple[int, str]:
        """将 sh.600519 转换为 mootdx 格式 (market, code)。"""
        prefix, code = ticker.split(".")
        market = 1 if prefix == "sh" else 0  # 1=沪市, 0=深市
        return market, code

    # ── 核心接口实现 ──────────────────────────────────────────

    def fetch_kline(
        self,
        ticker: str,
        start_date: str,
        end_date: str,
        frequency: str = "d",
        adjustflag: str = "1",
    ) -> KlineData | None:
        try:
            self._ensure_client()
            _market, code = self._ticker_to_mootdx(ticker)

            # mootdx freq: 0=5min, 1=15min, 2=30min, 3=60min, 8=daily
            freq_map = {"d": 8, "5min": 0, "15min": 1, "30min": 2, "60min": 3}
            mootdx_freq = freq_map.get(frequency, 8)

            df = self._client.bars(
                symbol=code,
                start=0,
                end=500,  # 最多拉 500 根
                freq=mootdx_freq,
            )
            if df is None or df.empty:
                return None

            # mootdx 返回列：datetime, open, high, low, close, volume, amount
            return KlineData(
                timestamps=pd_to_datetime_safe(df["datetime"].tolist()),
                open=df["open"].astype(float).tolist(),
                high=df["high"].astype(float).tolist(),
                low=df["low"].astype(float).tolist(),
                close=df["close"].astype(float).tolist(),
                volume=df["vol"].astype(float).tolist(),
                amount=df["amount"].astype(float).tolist(),
            )
        except ImportError:
            raise
        except Exception as e:
            logger.warning(f"{self.name} K 线拉取异常 {ticker}: {str(e)[:200]}")
            return None

    def fetch_quote(self, ticker: str) -> RealtimeQuote | None:
        try:
            self._ensure_client()
            market, code = self._ticker_to_mootdx(ticker)

            quotes = self._client.quotes(symbols=[(market, code)])
            if not quotes or len(quotes) == 0:
                return None

            q = quotes[0]
            return RealtimeQuote(
                ticker=ticker,
                price=safe_float(q.get("price")) if isinstance(q, dict) else None,
                source=self.name,
            )
        except ImportError:
            raise
        except Exception as e:
            logger.debug(f"{self.name} 实时行情异常 {ticker}: {str(e)[:100]}")
            return None

    def fetch_metadata(self, ticker: str) -> StockMetadata | None:
        """Mootdx 不提供完整的基本面元数据。"""
        return None

    def health_check(self) -> bool:
        try:
            self._ensure_client()
            df = self._client.bars(symbol="600519", start=0, end=5, freq=8)
            return df is not None and not df.empty
        except Exception as e:
            logger.debug(f"mootdx health check 异常: {e}")
            return False
