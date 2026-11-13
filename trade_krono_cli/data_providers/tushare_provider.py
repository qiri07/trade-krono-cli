"""data_providers.tushare_provider — Tushare Pro 数据源实现。

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
from typing import Any

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
            msg = (
                "Tushare 数据源需要 TUSHARE_TOKEN 环境变量。"
                "请在 .env 中设置 TUSHARE_TOKEN=your_token"
            )
            raise RuntimeError(
                msg,
            )
        cls._token = token
        try:
            import tushare as ts  # type: ignore

            ts.set_token(cls._token)
            cls._ts = ts
        except ImportError:
            msg = "tushare 未安装，无法使用 tushare 数据源。请运行: uv add tushare"
            raise RuntimeError(
                msg,
            )

    # ── 核心接口实现 ──────────────────────────────────────────

    @staticmethod
    def _to_ts_code(ticker: str) -> str | None:
        """把内部 ticker 转换为 tushare ts_code（600519.SH / 920001.BJ）。

        港股（hk.）tushare pro_bar 不支持，返回 None 让工厂降级到 akshare_hk。
        """
        ticker = ticker.strip()
        if ticker.startswith("hk."):
            return None
        if ticker.startswith("sh."):
            return f"{ticker[3:]}.SH"
        if ticker.startswith("sz."):
            return f"{ticker[3:]}.SZ"
        if ticker.startswith("bj."):
            return f"{ticker[3:]}.BJ"
        if ticker.endswith(".SH") or ticker.endswith(".SZ") or ticker.endswith(".BJ"):
            return ticker.upper()
        # 裸代码：按交易所规则推断
        if ticker.startswith(("6", "5", "900")):
            return f"{ticker}.SH"
        if ticker.startswith(("43", "83", "87", "920")):
            return f"{ticker}.BJ"
        return f"{ticker}.SZ"

    def fetch_kline(
        self,
        ticker: str,
        start_date: str,
        end_date: str,
        frequency: str = "d",
        adjustflag: str = "1",
    ) -> KlineData | None:
        try:
            self._ensure_import()
            # tushare 代码格式: 600519.SH / 000858.SZ / 920001.BJ
            ts_code = self._to_ts_code(ticker)
            if ts_code is None:
                return None

            # tushare adj 语义：None=不复权 / "qfq"=前复权 / "hfq"=后复权
            # adjustflag：0=不复权 / 1=前复权 / 2=后复权（与 baostock 一致）
            adj_map: dict[str, str | None] = {"0": None, "1": "qfq", "2": "hfq"}
            adj = adj_map.get(adjustflag, "qfq")

            df = self._ts.pro_bar(
                ts_code=ts_code,
                start_date=start_date,
                end_date=end_date,
                adj=adj,
            )
            if df is None or df.empty:
                return None

            return KlineData(
                timestamps=pd_to_datetime_safe(df["trade_date"].tolist()),
                open=df["open"].astype(float).tolist(),
                high=df["high"].astype(float).tolist(),
                low=df["low"].astype(float).tolist(),
                close=df["close"].astype(float).tolist(),
                # tushare vol 单位为手（100 股）、amount 单位为千元，统一换算为
                # 股 / 元，与 baostock、腾讯等主流来源保持一致。
                volume=(df["vol"].astype(float) * 100).tolist(),
                amount=(df["amount"].astype(float) * 1000).tolist(),
            )
        except ImportError:
            raise
        except Exception as e:
            logger.warning(f"{self.name} K 线拉取异常 {ticker}: {str(e)[:200]}")
            return None

    def fetch_quote(self, ticker: str) -> RealtimeQuote | None:
        try:
            self._ensure_import()
            ts_code = self._to_ts_code(ticker)
            if ts_code is None:
                return None
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

    def fetch_metadata(self, ticker: str) -> StockMetadata | None:
        try:
            self._ensure_import()
            ts_code = self._to_ts_code(ticker)
            if ts_code is None:
                return None

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
        except Exception as e:
            logger.debug(f"tushare health check 异常: {e}")
            return False


def pd_notna(value) -> bool:
    """检查 pandas NaN 安全。"""
    try:
        import pandas as pd

        return not pd.isna(value)
    except Exception as e:
        logger.debug(f"pandas isna 检查失败，降级处理: {e}")
        return value is not None
