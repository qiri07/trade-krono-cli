"""
data_providers.tushare_provider — Tushare Pro 数据源实现。

Tushare Pro 是专业的金融数据接口，需要注册获取 token。
支持 K 线、实时行情、完整基本面数据。

API 参考：
  - K 线: ts.pro_bar()
  - 实时行情: ts.realtime_quote()
  - 股票基本信息: ts.trade_cal() / ts.stock_basic()
  - 财务指标: ts.fina_indicator()
"""

from __future__ import annotations

import os
from typing import Any, Optional

from loguru import logger

from trade_krono_cli.data_providers.base import (
    DataProvider,
    KlineData,
    RealtimeQuote,
    StockMetadata,
)
from trade_krono_cli.utils import pd_to_datetime_safe, safe_float


class TushareProvider(DataProvider):
    """Tushare Pro 数据源实现。"""

    name = "tushare"
    supports_kline = True
    supports_quote = True
    supports_metadata = True

    # ── 懒加载 ────────────────────────────────────────────────

    _ts: Any = None
    _token: str = ""

    @classmethod
    def _ensure_import(cls) -> None:
        if cls._ts is not None:
            return
        token = os.getenv("TUSHARE_TOKEN", "")
        if not token:
            raise RuntimeError(
                "Tushare 数据源需要 TUSHARE_TOKEN 环境变量。"
                "请在 .env 中设置 TUSHARE_TOKEN=your_token"
            )
        cls._token = token
        try:
            import tushare as ts  # type: ignore

            ts.set_token(cls._token)
            cls._ts = ts
        except ImportError:
            raise RuntimeError(
                "tushare 未安装，无法使用 tushare 数据源。请运行: pip install tushare"
            )

    # ── 核心接口实现 ──────────────────────────────────────────

    def fetch_kline(
        self,
        ticker: str,
        start_date: str,
        end_date: str,
        frequency: str = "d",
        adjustflag: str = "1",
    ) -> Optional[KlineData]:
        try:
            self._ensure_import()
            # tushare 代码格式: 600519.SH / 000858.SZ
            ts_code = ticker.replace("sh.", ".SH").replace("sz.", ".SZ")

            df = self._ts.pro_bar(
                ts_code=ts_code,
                start_date=start_date,
                end_date=end_date,
                adj=adjustflag if adjustflag != "1" else "",
            )
            if df is None or df.empty:
                return None

            return KlineData(
                timestamps=pd_to_datetime_safe(df["trade_date"].tolist()),
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

    def fetch_quote(self, ticker: str) -> Optional[RealtimeQuote]:
        try:
            self._ensure_import()
            ts_code = ticker.replace("sh.", ".SH").replace("sz.", ".SZ")
            df = self._ts.realtime_quote(ts_code=ts_code)
            if df is None or df.empty:
                return None

            row = df.iloc[0]
            return RealtimeQuote(
                ticker=ticker,
                price=safe_float(row.get("last_close")) or safe_float(row.get("price")),
                pe=safe_float(row.get("pe")) or safe_float(row.get("pe_ttm")),
                pb=safe_float(row.get("pb")),
                market_cap=safe_float(row.get("total_mv")),
                source=self.name,
            )
        except ImportError:
            raise
        except Exception as e:
            logger.debug(f"{self.name} 实时行情异常 {ticker}: {str(e)[:100]}")
            return None

    def fetch_metadata(self, ticker: str) -> Optional[StockMetadata]:
        try:
            self._ensure_import()
            ts_code = ticker.replace("sh.", ".SH").replace("sz.", ".SZ")

            # 基本信息
            df = self._ts.stock_basic(
                ts_code=ts_code,
                fields="ts_code,symbol,name,area,industry,list_date,delist_date",
            )
            if df is None or df.empty:
                return None

            row = df.iloc[0]
            is_st = "ST" in str(row.get("name", ""))

            return StockMetadata(
                ticker=ticker,
                industry=str(row.get("industry")) if pd_notna(row.get("industry")) else None,
                ipo_date=str(row.get("list_date"))[:10] if pd_notna(row.get("list_date")) else None,
                out_date=str(row.get("delist_date"))[:10]
                if pd_notna(row.get("delist_date"))
                else None,
                is_st=is_st,
                source=self.name,
            )
        except ImportError:
            raise
        except Exception as e:
            logger.debug(f"{self.name} 元数据异常 {ticker}: {str(e)[:100]}")
            return None

    def health_check(self) -> bool:
        try:
            self._ensure_import()
            df = self._ts.stock_basic(
                exchange="",
                list_status="L",
                fields="ts_code",
            )
            return df is not None and not df.empty
        except Exception:
            return False


def pd_notna(value) -> bool:
    """检查 pandas NaN 安全。"""
    try:
        import pandas as pd

        return not pd.isna(value)
    except Exception:
        return value is not None
