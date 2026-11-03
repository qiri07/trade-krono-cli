"""data_providers.easyquotation_provider — easyquotation 数据源实现。

easyquotation 是免费开源的 A 股实时行情库，基于腾讯/新浪源。
无需注册 API Key。
覆盖范围：
  · 实时行情：easyquotation.use('tencent'|'sina').real([codes])
  · 不支持 K 线（daykline API 不稳定）
  · 不支持完整基本面元数据（实时行情中包含 PE/PB/市值，但字段不完整）

API 参考：
  - https://github.com/bin2coin/easyquotation

环境变量：无需配置（免费开源）。
"""

from __future__ import annotations

import os
import re
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

# 支持的行情源
_SUPPORTED_SOURCES = ("tencent", "sina")


class EasyQuotationProvider(DataProvider):
    """easyquotation 数据源实现。

    依赖 easyquotation 库（免费开源，无需 API Key）。
    主要用于实时行情快照，作为 tonghuashun/akshare 实时行情的降级备用。
    """

    name = "easyquotation"
    supports_kline = False
    supports_quote = True
    supports_metadata = False

    # ── 懒加载 ────────────────────────────────────────────────
    _eq: Any = None
    _source: str = ""

    @classmethod
    def _ensure_import(cls) -> None:
        """延迟导入 easyquotation 并初始化行情源。"""
        if cls._eq is not None:
            return
        try:
            import easyquotation as eq  # type: ignore

            cls._eq = eq
        except ImportError:
            msg = "easyquotation 未安装，无法使用 easyquotation 数据源。请运行: uv add easyquotation"
            raise RuntimeError(msg)

        # 读取配置的源，默认 tencent
        source = os.getenv("EASYQUOTATION_SOURCE", "tencent").strip().lower()
        if source not in _SUPPORTED_SOURCES:
            logger.warning(
                f"easyquotation 源 '{source}' 不支持，回退到 'tencent'（支持: {_SUPPORTED_SOURCES}）"
            )
            source = "tencent"
        cls._source = source
        logger.debug(f"easyquotation 初始化完成，源={source}")

    @staticmethod
    def _ticker_to_code(ticker: str) -> str | None:
        """把带前缀 ticker（sh.600519 / sz.000858 / bj.920000）转为 6 位裸代码。

        easyquotation 只接受 6 位数字代码，不需要交易所前缀。
        北交所和港股暂不支持，返回 None。
        """
        bare = ticker.split(".", 1)[-1] if "." in ticker else ticker
        if _STOCK_CODE_RE.match(bare):
            return bare
        return None

    # ── K 线（不支持）─────────────────────────────────────────

    def fetch_kline(
        self,
        ticker: str,
        start_date: str,
        end_date: str,
        frequency: str = "d",
        adjustflag: str = "1",
    ) -> KlineData | None:
        """easyquotation 不支持 K 线拉取。"""
        return None

    # ── 实时行情 ──────────────────────────────────────────────

    def fetch_quote(self, ticker: str) -> RealtimeQuote | None:
        """获取实时行情快照。

        通过配置的行情源（tencent/sina）获取单只股票的实时行情。
        """
        self._ensure_import()
        code = self._ticker_to_code(ticker)
        if code is None:
            return None

        try:
            quote = self._eq.use(self._source)
            data = quote.real([code])
        except Exception as e:
            logger.warning(f"easyquotation 实时行情异常 {ticker} (源={self._source}): {str(e)[:100]}")
            return None

        if not data or code not in data:
            return None

        info = data[code]
        # 不同源的字段名略有不同，统一映射
        price = safe_float(
            info.get("now")
            or info.get("最新价")
            or info.get("current_price")
        )
        pe = safe_float(
            info.get("PE")
            or info.get("市盈(动)")
            or info.get("动态市盈率")
        )
        pb = safe_float(info.get("PB") or info.get("市净率"))
        market_cap = safe_float(
            info.get("总市值")
            or info.get("total_market_cap")
        )
        turnover = safe_float(info.get("turnover") or info.get("换手率"))

        return RealtimeQuote(
            ticker=ticker,
            price=price,
            pe=pe,
            pb=pb,
            market_cap=market_cap,
            turnover=turnover,
            source=f"{self.name}:{self._source}",
        )

    # ── 基本面元数据（不支持完整）─────────────────────────────

    def fetch_metadata(self, ticker: str) -> StockMetadata | None:
        """easyquotation 不提供完整的基本面元数据，返回 None。

        实时行情中虽含 PE/PB/市值，但缺少行业、上市日期、ST 状态等关键字段，
        故不承担元数据维度，由其他 Provider（tonghuashun/efinance/baostock）负责。
        """
        return None
