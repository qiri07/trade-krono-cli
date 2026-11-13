"""data_providers.mootdx_provider — MootDx 数据源实现。

MootDx 是开源的行情数据接口，支持 level-2 数据。
无需注册，但需要安装 mootdx 包。

API 参考：
  - K 线: MdxApi.factory().get_security_bars()
  - 实时行情: MdxApi.factory().get_security_quotes()
"""

from __future__ import annotations

from typing import Any

import pandas as pd
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
            msg = "mootdx 未安装，无法使用 mootdx 数据源。请运行: uv add mootdx"
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
        """将 sh.600519 转换为 mootdx 格式 (market, code)。

        北交所（bj.）mootdx 服务器不覆盖，返回 -1 让调用方直接跳过。
        """
        prefix, code = ticker.split(".")
        if prefix == "bj":
            return -1, code
        market = 1 if prefix == "sh" else 0  # 1=沪市, 0=深市
        return market, code

    # ── 核心接口实现 ──────────────────────────────────────────

    @staticmethod
    def _estimate_bars(start_date: str, end_date: str, frequency: str) -> int:
        """按日期区间估算需要的 K 线条数（含 20% 缓冲）。

        仅用于日志/内部预算参考，实际取数由 :meth:`_fetch_bars_in_range`
        分页完成。
        """
        per_day = {
            "d": 1,
            "5min": 48,
            "15min": 16,
            "30min": 8,
            "60min": 4,
            "1min": 240,
        }.get(frequency, 1)
        try:
            days = max(
                1,
                (pd.Timestamp(end_date) - pd.Timestamp(start_date)).days,
            )
        except (ValueError, TypeError):
            days = 1
        return int(days * per_day * 1.2) + 5

    def _fetch_bars_in_range(
        self,
        code: str,
        mootdx_freq: int,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame | None:
        """按日期区间向 mootdx 分页取 K 线。

        mootdx ``bars`` 只有 ``start``/``offset`` 两个滚动窗口参数（无日期参数），
        此前只取最近 800 根就当作区间结果返回，导致历史窗口被静默截断。
        这里从最新数据向前翻页，直到覆盖 start_date 或达到上限。
        """
        frames: list[pd.DataFrame] = []
        pos = 0
        max_pos = 7200  # 上限：约 30 年日线，避免异常请求下死循环
        while pos <= max_pos:
            df = self._client.bars(symbol=code, frequency=mootdx_freq, start=pos, offset=800)
            if df is None or df.empty:
                break
            frames.append(df)
            idx = pd.to_datetime(df.index)
            if idx.min() <= pd.Timestamp(start_date):
                break  # 已覆盖到请求起点
            pos += len(df)
            if len(df) < 800:
                break  # 没有更早的数据了

        if not frames:
            return None
        merged = pd.concat(frames)
        # 索引可能重复（分页重叠），去重并按时间排序
        merged = merged[~merged.index.duplicated(keep="last")].sort_index()
        return merged

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
            market, code = self._ticker_to_mootdx(ticker)
            if market < 0:
                logger.debug(f"{self.name} 不支持北交所标的 {ticker}，跳过")
                return None

            # mootdx freq: 0=5min, 1=15min, 2=30min, 3=60min, 8=daily
            freq_map = {"d": 8, "5min": 0, "15min": 1, "30min": 2, "60min": 3}
            mootdx_freq = freq_map.get(frequency, 8)

            df = self._fetch_bars_in_range(code, mootdx_freq, start_date, end_date)
            if df is None or df.empty:
                return None

            # mootdx 的 index 为 datetime；无 datetime 列时先落成列
            if "datetime" not in df.columns:
                df = df.rename_axis("datetime").reset_index()
            # 按请求区间截断：mootdx 只支持滚动窗口，必须自行过滤，
            # 否则会把区间外的数据当作区间结果返回。
            idx = pd.to_datetime(df["datetime"])
            mask = (idx >= pd.Timestamp(start_date)) & (idx <= pd.Timestamp(end_date))
            df = df[mask]
            if df.empty:
                logger.debug(
                    f"{self.name} {ticker} 请求区间 {start_date}~{end_date} 无数据"
                )
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
