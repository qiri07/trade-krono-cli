"""data_providers.efinance_provider — efinance 数据源实现。

efinance 是免费开源的 A 股数据库，无需注册 API Key。
覆盖范围：
  · K 线：ef.stock.get_quote_history(code, beg, end)
  · 实时行情：ef.stock.get_latest_quote([codes])
  · 基本面：ef.stock.get_base_info(code)（行业、PE/PB、上市日期、ROE 等）

API 参考：
  - https://github.com/mickey991/efinance

环境变量：无需配置（免费开源）。
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from loguru import logger

from trade_krono_cli.data_providers.base import (
    DataProvider,
    KlineData,
    RealtimeQuote,
    StockMetadata,
)
from trade_krono_cli.utils import safe_float

# 6 位数字股票代码正则
_STOCK_CODE_RE = re.compile(r"^\d{6}$")


class EFinanceProvider(DataProvider):
    """efinance 数据源实现。

    依赖 efinance 库（免费开源，无需 API Key）。
    支持 K 线、行情快照、基本面元数据三种数据维度。
    """

    name = "efinance"
    supports_kline = True
    supports_quote = True
    supports_metadata = True

    # ── 懒加载 ────────────────────────────────────────────────
    _ef: Any = None

    @classmethod
    def _ensure_import(cls) -> None:
        """延迟导入 efinance。"""
        if cls._ef is not None:
            return
        try:
            import efinance as ef  # type: ignore

            cls._ef = ef
        except ImportError:
            msg = "efinance 未安装，无法使用 efinance 数据源。请运行: uv add efinance"
            raise RuntimeError(msg)

    @staticmethod
    def _ticker_to_code(ticker: str) -> str | None:
        """把带前缀 ticker（sh.600519 / sz.000858 / bj.920000）转为 6 位裸代码。

        efinance 只接受 6 位数字代码，不需要交易所前缀。
        北交所（8 开头/9 开头）和港股（非纯数字）暂不支持，返回 None。
        """
        bare = ticker.split(".", 1)[-1] if "." in ticker else ticker
        if _STOCK_CODE_RE.match(bare):
            return bare
        return None

    # ── K 线 ──────────────────────────────────────────────────

    def fetch_kline(
        self,
        ticker: str,
        start_date: str,
        end_date: str,
        frequency: str = "d",
        adjustflag: str = "1",
    ) -> KlineData | None:
        """拉取 K 线数据。

        efinance 仅支持日线（frequency="d"），不支持分钟线。
        beg/end 格式为 YYYYMMDD。
        """
        self._ensure_import()
        code = self._ticker_to_code(ticker)
        if code is None:
            logger.debug(f"efinance 不支持 ticker 格式: {ticker}")
            return None

        if frequency != "d":
            logger.debug(f"efinance 仅支持日线频率，请求了 {frequency}")
            return None

        try:
            ef = self._ef
            beg = start_date.replace("-", "")
            end = end_date.replace("-", "")
            df = ef.stock.get_quote_history(code, beg=beg, end=end)
        except Exception as e:
            logger.warning(f"efinance K 线拉取异常 {ticker}: {str(e)[:100]}")
            return None

        if df is None or df.empty:
            return None

        # 标准化列名
        col_map = {
            "日期": "timestamps",
            "开盘": "open",
            "收盘": "close",
            "最高": "high",
            "最低": "low",
            "成交量": "volume",
            "成交额": "amount",
        }
        missing = [c for c in col_map if c not in df.columns]
        if missing:
            logger.warning(f"efinance K 线缺少列 {missing} ({ticker})")
            return None

        df = df.rename(columns=col_map)
        df["timestamps"] = pd_to_datetime_safe(df["timestamps"])
        for c in ("open", "high", "low", "close", "volume", "amount"):
            df[c] = df[c].astype(float)

        df = df.sort_values("timestamps").reset_index(drop=True)
        return KlineData(
            timestamps=df["timestamps"].tolist(),
            open=df["open"].tolist(),
            high=df["high"].tolist(),
            low=df["low"].tolist(),
            close=df["close"].tolist(),
            volume=df["volume"].tolist(),
            amount=df["amount"].tolist(),
        )

    # ── 实时行情 ──────────────────────────────────────────────

    def fetch_quote(self, ticker: str) -> RealtimeQuote | None:
        """获取实时行情快照。

        efinance 支持批量获取，这里单只调用。
        """
        self._ensure_import()
        code = self._ticker_to_code(ticker)
        if code is None:
            return None

        try:
            ef = self._ef
            df = ef.stock.get_latest_quote([code])
        except Exception as e:
            logger.warning(f"efinance 实时行情异常 {ticker}: {str(e)[:100]}")
            return None

        if df is None or df.empty:
            return None

        row = df.iloc[0]
        return RealtimeQuote(
            ticker=ticker,
            price=safe_float(row.get("最新价")),
            pe=safe_float(row.get("动态市盈率")),
            pb=safe_float(row.get("市净率")),
            market_cap=safe_float(row.get("总市值")),
            turnover=safe_float(row.get("换手率")),
            source=self.name,
        )

    # ── 基本面元数据 ──────────────────────────────────────────

    def fetch_metadata(self, ticker: str) -> StockMetadata | None:
        """获取股票基础元数据。

        efinance 提供：股票名称、所处行业、市盈率(动)、市净率、ROE、毛利率等。
        注意：efinance 不直接提供上市日期，ipo_date 为 None。
        """
        self._ensure_import()
        code = self._ticker_to_code(ticker)
        if code is None:
            return None

        try:
            ef = self._ef
            # 传入列表可获得 DataFrame（单字符串会返回 Series）
            result = ef.stock.get_base_info([code])
        except Exception as e:
            logger.warning(f"efinance 基本面异常 {ticker}: {str(e)[:100]}")
            return None

        if result is None:
            return None

        import pandas as pd

        # 单字符串调用返回 Series，列表调用返回 DataFrame
        if isinstance(result, pd.Series):
            row = result
        elif isinstance(result, pd.DataFrame) and not result.empty:
            row = result.iloc[0]
        else:
            return None

        industry = _get_cell(row, "所处行业")
        pe_ttm = safe_float(_get_cell(row, "市盈率(动)") or _get_cell(row, "市盈率"))
        pb = safe_float(_get_cell(row, "市净率"))

        return StockMetadata(
            ticker=ticker,
            industry=industry,
            industry_code=None,
            pe_ttm=pe_ttm,
            pb=pb,
            ipo_date=None,  # efinance base_info 不直接提供上市日期
            out_date=None,
            is_st=False,  # efinance 未提供 ST 标记，由上层通过名称判断
            source=self.name,
        )


def pd_to_datetime_safe(series: Any) -> Any:
    """把 pandas Series 的日期列转为 datetime 对象，容错处理。"""
    import pandas as pd

    try:
        return pd.to_datetime(series).tolist()
    except Exception:
        return [datetime.strptime(str(v), "%Y-%m-%d") for v in series]


def _get_cell(row: Any, col: str) -> Any:
    """安全获取 DataFrame 单行的单元格值。"""
    try:
        val = row.get(col)
        if val is None or (isinstance(val, float) and val != val):  # NaN
            return None
        return val
    except Exception:
        return None
