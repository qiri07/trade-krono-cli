"""data_providers — 多数据源抽象层。

提供统一的 DataProvider 接口，支持 baostock / akshare / mootdx / tushare
四种 A 股数据源，并内置主备降级机制。

架构：
  应用层（data.py / stock_filter.py / abnormal_stock.py）
    → DataProviderFactory.get_provider(name) → 具体 Provider 实例
      → 各 Provider 内部处理不同 API 的调用差异
      → 失败时 Factory 自动切换备用源
  缓存层（cache.py）— 对上层透明，key 格式不变
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from datetime import datetime

    import pandas as pd

# ═══════════════════════════════════════════════════════
# 标准化数据模型
# ═══════════════════════════════════════════════════════


@dataclass
class KlineData:
    """标准化的 K 线数据。

    所有 Provider 返回此格式，上层无需关心数据来源。
    None 字段表示该 Provider 不支持此维度。
    """

    timestamps: list[datetime] = field(default_factory=list)
    open: list[float] = field(default_factory=list)
    high: list[float] = field(default_factory=list)
    low: list[float] = field(default_factory=list)
    close: list[float] = field(default_factory=list)
    volume: list[float] = field(default_factory=list)
    amount: list[float] = field(default_factory=list)

    @property
    def length(self) -> int:
        return len(self.timestamps)

    @property
    def is_empty(self) -> bool:
        return self.length == 0

    def to_dataframe(self) -> pd.DataFrame:
        """转换为 DataFrame（供缓存和上层使用）。"""
        import pandas as pd

        return pd.DataFrame(
            {
                "timestamps": self.timestamps,
                "open": self.open,
                "high": self.high,
                "low": self.low,
                "close": self.close,
                "volume": self.volume,
                "amount": self.amount,
            },
        ).reset_index(drop=True)

    @classmethod
    def from_dataframe(cls, df) -> KlineData:
        """从 DataFrame 构造 KlineData。"""
        import pandas as pd

        ts = pd.to_datetime(df["timestamps"])
        return cls(
            timestamps=ts.tolist(),
            open=df["open"].astype(float).tolist(),
            high=df["high"].astype(float).tolist(),
            low=df["low"].astype(float).tolist(),
            close=df["close"].astype(float).tolist(),
            volume=df["volume"].astype(float).tolist(),
            amount=df["amount"].astype(float).tolist(),
        )


@dataclass
class RealtimeQuote:
    """实时行情快照。

    字段均可为 None（数据不可用时）。
    """

    ticker: str = ""
    name: str = ""  # 股票名称（部分数据源不提供，默认空）
    price: float | None = None  # 当前价（元）
    pe: float | None = None  # 市盈率（动态）
    pb: float | None = None  # 市净率
    market_cap: float | None = None  # 总市值（亿元）
    turnover: float | None = None  # 换手率（%）
    source: str = ""  # 数据来源标识


@dataclass
class StockMetadata:
    """股票基础元数据。

    用于过滤、风险评分等场景。
    """

    ticker: str = ""
    industry: str | None = None  # 行业名称（如 "银行"）
    industry_code: str | None = None  # 行业代码
    pe_ttm: float | None = None  # 市盈率 TTM
    pb: float | None = None  # 市净率
    dividend_yield: float | None = None  # 股息率（%），未知时为 None
    ipo_date: str | None = None  # 上市日期 YYYY-MM-DD
    out_date: str | None = None  # 退市日期 YYYY-MM-DD
    is_st: bool = False  # 是否 ST 标的
    source: str = ""  # 数据来源标识


# ═══════════════════════════════════════════════════════
# Provider 抽象基类
# ═══════════════════════════════════════════════════════


class DataProvider(ABC):
    """数据源抽象基类。

    所有具体 Provider 必须实现以下方法：
      - fetch_kline()       : 拉取 K 线数据
      - fetch_quote()       : 获取实时行情快照
      - fetch_metadata()    : 获取股票基础元数据
      - health_check()      : 检查数据源可用性

    子类通过设置类属性声明支持的数据维度：
      supports_kline  = True/False
      supports_quote  = True/False
      supports_metadata = True/False
    """

    # 数据源名称标识
    name: str = "base"

    # 支持的数据维度（子类可覆盖）
    supports_kline: bool = True
    supports_quote: bool = True
    supports_metadata: bool = True

    # ── 核心接口 ─────────────────────────────────────────────

    @abstractmethod
    def fetch_kline(
        self,
        ticker: str,
        start_date: str,
        end_date: str,
        frequency: str = "d",
        adjustflag: str = "1",
    ) -> KlineData | None:
        """拉取 K 线数据。

        Parameters
        ----------
        ticker : 股票代码（已归一化，如 sh.600519）
        start_date : 起始日期 YYYY-MM-DD
        end_date : 结束日期 YYYY-MM-DD
        frequency : 频率 "d"/"5min"/"15min"/"30min"/"60min"
        adjustflag : 复权因子 "0"/"1"/"2"

        Returns
        -------
        KlineData 或 None（失败时）

        """
        ...

    @abstractmethod
    def fetch_quote(self, ticker: str) -> RealtimeQuote | None:
        """获取实时行情快照。

        Returns
        -------
        RealtimeQuote 或 None

        """
        ...

    @abstractmethod
    def fetch_metadata(self, ticker: str) -> StockMetadata | None:
        """获取股票基础元数据（行业、PE/PB、上市/退市日期、ST 状态）。

        Returns
        -------
        StockMetadata 或 None

        """
        ...

    def health_check(self) -> bool:
        """检查数据源是否可用。

        默认实现：尝试获取自身基本信息，成功则可用。
        子类应覆盖以提供更精确的健康检查。
        """
        try:
            result = self.fetch_metadata("sh.600519")
            return result is not None
        except Exception as e:
            logger.debug(f"base provider health check 异常: {e}")
            return False

    # ── 便捷方法 ─────────────────────────────────────────────

    def fetch_kline_as_df(
        self,
        ticker: str,
        start_date: str,
        end_date: str,
        frequency: str = "d",
        adjustflag: str = "1",
    ) -> Any | None:
        """拉取 K 线并直接返回 DataFrame（便捷方法）。

        等价于 fetch_kline() + KlineData.to_dataframe()。
        """
        data = self.fetch_kline(ticker, start_date, end_date, frequency, adjustflag)
        if data is None:
            return None
        return data.to_dataframe()

    # ── 批量获取（默认实现：并发调用单只票方法）───────────────

    def fetch_quotes_batch(
        self,
        tickers: list[str],
        max_workers: int = 8,
    ) -> dict[str, RealtimeQuote]:
        """批量获取实时行情。

        默认实现使用 ThreadPoolExecutor 并发调用 :meth:`fetch_quote`。
        子类如 AkShareProvider 可覆盖为全市场快照复用，避免逐票 HTTP 请求。

        Parameters
        ----------
        tickers : list[str]
            股票代码列表。
        max_workers : int
            并发线程数，默认 8。

        Returns
        -------
        dict[str, RealtimeQuote]
            成功获取的 ticker → 行情映射。
        """
        results: dict[str, RealtimeQuote] = {}
        if not tickers:
            return results

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(self.fetch_quote, t): t for t in tickers}
            for future in as_completed(futures):
                ticker = futures[future]
                try:
                    quote = future.result()
                    if quote is not None:
                        results[ticker] = quote
                except Exception as e:  # noqa: BLE001
                    logger.debug(f"{self.name} 批量行情 {ticker} 异常: {e}")
        return results

    def fetch_metadata_batch(
        self,
        tickers: list[str],
        max_workers: int = 8,
    ) -> dict[str, StockMetadata]:
        """批量获取股票元数据。

        默认实现使用 ThreadPoolExecutor 并发调用 :meth:`fetch_metadata`。

        Parameters
        ----------
        tickers : list[str]
            股票代码列表。
        max_workers : int
            并发线程数，默认 8。

        Returns
        -------
        dict[str, StockMetadata]
            成功获取的 ticker → 元数据映射。
        """
        results: dict[str, StockMetadata] = {}
        if not tickers:
            return results

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(self.fetch_metadata, t): t for t in tickers}
            for future in as_completed(futures):
                ticker = futures[future]
                try:
                    meta = future.result()
                    if meta is not None:
                        results[ticker] = meta
                except Exception as e:  # noqa: BLE001
                    logger.debug(f"{self.name} 批量元数据 {ticker} 异常: {e}")
        return results


# 导入 Any 用于类型注解
from typing import Any  # noqa: E402
