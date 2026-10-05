"""data_providers.akshare_hk_provider — 港股数据源（多源备选）。

数据源优先级：
  1. akshare.stock_hk_daily（新浪源，全量历史 + 复权）—— 主源
  2. akshare.stock_hk_spot（新浪源，全市场实时行情）—— 备用/行情
  3. 腾讯分钟线 API（web.ifzq.gtimg.cn）—— 最后兜底

无需注册 key，覆盖港股全量历史行情。
所有接口均为免费公开接口，不依赖东方财富（已被网络阻断）。
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

import pandas as pd
from loguru import logger

from trade_krono_cli.data_providers.base import (
    DataProvider,
    KlineData,
    RealtimeQuote,
    StockMetadata,
)
from trade_krono_cli.utils import pd_to_datetime_safe

if TYPE_CHECKING:
    pass


class AkShareHKProvider(DataProvider):
    """港股多源数据 Provider（akshare 新浪源 + 腾讯备用）。"""

    name = "akshare_hk"
    supports_kline = True
    supports_quote = True
    supports_metadata = False

    # ── 懒加载 ────────────────────────────────────────────────

    _ak: Any = None
    _req: Any = None

    @classmethod
    def _ensure_import(cls) -> None:
        if cls._ak is not None:
            return
        try:
            import akshare as ak  # type: ignore
            import requests as req  # type: ignore

            cls._ak = ak
            cls._req = req
        except ImportError:
            msg = "akshare 未安装，无法使用 akshare_hk 数据源。请运行: uv add akshare"
            raise RuntimeError(msg)

    # ── 工具方法 ────────────────────────────────────────────────

    @staticmethod
    def _ticker_to_symbol(ticker: str) -> str:
        """将 hk.00700 格式转换为纯数字代码。"""
        return ticker.rsplit(".", maxsplit=1)[-1]

    @classmethod
    def _fetch_via_akshare_daily(cls, symbol: str, adjustflag: str = "qfq") -> Any | None:
        """通过 akshare.stock_hk_daily 拉取历史 K 线（主源）。"""
        try:
            cls._ensure_import()
            # akshare stock_hk_daily adjust 参数: ""=不复权, "qfq"=前复权, "hfq"=后复权
            adjust_map = {"0": "", "1": "qfq", "2": "hfq"}
            adjust = adjust_map.get(adjustflag, "qfq")
            df = cls._ak.stock_hk_daily(symbol=symbol, adjust=adjust)  # type: ignore[attr-defined]
            if df is None or df.empty:
                return None
            return df
        except Exception as e:
            logger.debug(f"akshare_daily 异常 {symbol}: {e}")
            return None

    @classmethod
    def _fetch_via_sina_direct(cls, symbol: str) -> Any | None:
        """直接请求新浪财经 K 线接口（绕过 akshare JS 解密，作为备用）。"""
        try:
            cls._ensure_import()
            url = f"https://finance.sina.com.cn/stock/hkstock/{symbol}/klc2_kl.js"
            r = cls._req.get(url, timeout=10, headers={"Referer": "https://finance.sina.com.cn"})
            if r.status_code != 200 or not r.text.strip():
                return None
            # 解析 var KLC_K2_XXXXX="ENCODED_DATA";
            match = re.search(r'var KLC_K2_\w+="([^"]+)"', r.text)
            if not match:
                logger.debug(f"sina_direct 解析失败 {symbol}")
                return None
            # 尝试 base64 解码（与 akshare MiniRacer 解密等价）
            import base64

            encoded = match.group(1)
            try:
                decoded = base64.b64decode(encoded).decode("utf-8")
            except Exception:
                return None
            # 解析 JSON 数组
            import json

            rows = json.loads(decoded)
            if not rows:
                return None
            import pandas as pd

            df = pd.DataFrame(rows)
            required_cols = {"date", "open", "high", "low", "close", "volume"}
            if not required_cols.issubset(df.columns):
                logger.debug(f"sina_direct 列不完整 {symbol}: {df.columns.tolist()}")
                return None
            return df
        except Exception as e:
            logger.debug(f"sina_direct 异常 {symbol}: {e}")
            return None

    @classmethod
    def _fetch_via_tencent_minute(cls, symbol: str) -> Any | None:
        """通过腾讯分钟线 API 获取近期数据（最后兜底）。"""
        try:
            cls._ensure_import()
            url = f"http://web.ifzq.gtimg.cn/appstock/app/minute/query?_var=test&code=hk{symbol}"
            r = cls._req.get(url, timeout=10)
            if r.status_code != 200:
                return None
            text = r.text.replace("test=", "")
            import json

            data = json.loads(text)
            if data.get("code") != 0:
                return None
            stock_data = data.get("data", {}).get(f"hk{symbol}", {})
            minutes = stock_data.get("data", {}).get("data", [])
            if not minutes:
                return None
            # 格式: "HHMM price volume timestamp"
            rows = []
            import time

            for m in minutes:
                parts = m.split(" ")
                if len(parts) < 4:
                    continue
                _, price, vol, ts = parts[0], parts[1], parts[2], parts[3]
                # 生成当日日期（分钟线无日期字段，用当天）
                dt = time.strftime("%Y-%m-%d", time.localtime(float(ts)))
                rows.append(
                    {
                        "date": dt,
                        "open": price,
                        "high": price,
                        "low": price,
                        "close": price,
                        "volume": vol,
                    }
                )
            if not rows:
                return None
            import pandas as pd

            return pd.DataFrame(rows)
        except Exception as e:
            logger.debug(f"tencent_minute 异常 {symbol}: {e}")
            return None

    # ── 核心接口实现 ──────────────────────────────────────────

    def fetch_kline(
        self,
        ticker: str,
        start_date: str | None = None,
        end_date: str | None = None,
        frequency: str = "d",
        adjustflag: str = "1",
    ) -> KlineData | None:
        """拉取港股日线 K 线数据（多源备选）。

        Parameters
        ----------
        ticker : str
            港股 ticker（格式：hk.00700）
        start_date : str | None
            起始日期 YYYY-MM-DD
        end_date : str | None
            结束日期 YYYY-MM-DD
        frequency : str
            频率，默认 "d"（日线）
        adjustflag : str
            复权方式，"1"=前复权

        Returns
        -------
        KlineData | None
        """
        if frequency != "d":
            logger.debug(f"{self.name} 暂不支持频率 {frequency}，跳过")
            return None

        symbol = self._ticker_to_symbol(ticker)
        df: Any | None = None

        # 源 1: akshare stock_hk_daily（主源，最稳定）
        df = self._fetch_via_akshare_daily(symbol, adjustflag)
        if df is not None and not df.empty:
            logger.debug(f"{self.name} 主源命中: {ticker} ({len(df)} 行)")
        else:
            # 源 2: 新浪直连（备用）
            df = self._fetch_via_sina_direct(symbol)
            if df is not None and not df.empty:
                logger.debug(f"{self.name} 备用源1命中: {ticker} ({len(df)} 行)")
            else:
                # 源 3: 腾讯分钟线（最后兜底，仅近期）
                df = self._fetch_via_tencent_minute(symbol)
                if df is not None and not df.empty:
                    logger.debug(f"{self.name} 备用源2命中: {ticker} ({len(df)} 行)")

        if df is None or df.empty:
            logger.warning(f"{self.name} 所有源均无数据: {ticker}")
            return None

        # 按日期过滤
        if "date" not in df.columns:
            logger.warning(f"{self.name} 数据缺少 date 列: {ticker}")
            return None

        df["date"] = pd_to_datetime_safe(df["date"])
        if start_date is not None:
            df = df[df["date"] >= start_date]
        if end_date is not None:
            df = df[df["date"] <= end_date]

        if df.empty:
            return None

        amount_col = "amount" if "amount" in df.columns else None

        timestamps = pd_to_datetime_safe(df["date"].tolist())
        return KlineData(
            timestamps=timestamps,
            open=df["open"].astype(float).tolist(),
            high=df["high"].astype(float).tolist(),
            low=df["low"].astype(float).tolist(),
            close=df["close"].astype(float).tolist(),
            volume=df["volume"].astype(float).tolist(),
            amount=df[amount_col].astype(float).tolist() if amount_col else [0.0] * len(timestamps),
        )

    def fetch_quote(self, ticker: str) -> "RealtimeQuote | None":
        """通过 akshare.stock_hk_spot 获取实时行情。"""
        try:
            self._ensure_import()
            symbol = self._ticker_to_symbol(ticker)
            df = self._ak.stock_hk_spot()  # type: ignore[attr-defined]
            if df is None or df.empty:
                return None
            row = df[df["代码"] == symbol]
            if row.empty:
                return None
            r = row.iloc[0]
            return RealtimeQuote(
                ticker=ticker,
                price=float(r["最新价"]) if pd.notna(r["最新价"]) else 0.0,  # type: ignore[arg-type]
            )
        except Exception as e:
            logger.debug(f"{self.name} 实时行情获取失败 {ticker}: {e}")
            return None

    def fetch_metadata(self, ticker: str) -> "StockMetadata | None":
        """港股 Provider 不提供元数据。"""
        return None

    def health_check(self) -> bool:
        """检查港股数据源是否可用（依次尝试各源）。"""
        # 先试主源
        try:
            self._ensure_import()
            df = self._fetch_via_akshare_daily("00700", "qfq")
            if df is not None and not df.empty:
                return True
        except Exception:
            pass
        # 再试备用源
        try:
            df = self._fetch_via_sina_direct("00700")
            if df is not None and not df.empty:
                return True
        except Exception:
            pass
        # 最后试腾讯
        try:
            df = self._fetch_via_tencent_minute("00700")
            if df is not None and not df.empty:
                return True
        except Exception:
            pass
        logger.debug(f"{self.name} 所有数据源均不可用")
        return False
