"""data_providers.adk_data_provider — a-stock-data (adata) 数据源实现。

a-stock-data 是 A 股全栈数据工具包，覆盖 15 层、87 个端点、34 个数据源。
它提供 K 线、实时行情、研报、龙虎榜、北向资金、板块资金流、解禁日历、
宏观指数、公告等丰富数据。

数据源对标：https://github.com/simonlin1212/a-stock-data

无需注册 API Key，所有端点均为免费开源。

实际使用时请根据安装的 a-stock-data 包名调整导入路径（常见库名：
a-stock-data / astock_data / adata）。
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


class AdkDataProvider(DataProvider):
    """a-stock-data (adata) 数据源实现。

    名称：adata
    支持 K 线、实时行情、基本面元数据。
    由于 a-stock-data 的具体 API 签名随版本可能变化，本实现采用
    最常见的端点名称；如 API 变更，请根据实际安装包调整导入路径
    和调用签名。
    """

    name = "adata"
    supports_kline = True
    supports_quote = True
    supports_metadata = True

    _adk: Any = None

    @classmethod
    def _ensure_import(cls) -> None:
        if cls._adk is not None:
            return
        try:
            # 尝试常见的包名；实际部署时请根据安装包调整
            try:
                import astock_data as adk  # type: ignore
            except ImportError:
                import a_stock_data as adk  # type: ignore
            cls._adk = adk
        except ImportError:
            msg = (
                "a-stock-data 未安装，无法使用 adk 数据源。"
                "请运行: uv add a-stock-data (或根据实际包名调整)"
            )
            raise RuntimeError(msg)

    # ── 内部转换工具 ──────────────────────────────────────────

    @staticmethod
    def _ticker_to_adk(ticker: str) -> str:
        """将 sh.600519 格式转换为 600519 格式。"""
        return ticker.rsplit(".", maxsplit=1)[-1]

    @staticmethod
    def _adk_to_ticker(code: str) -> str:
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
        """拉取 K 线数据。

        使用 a-stock-data 的 K 线接口获取历史数据。

        Parameters
        ----------
        ticker : str
            股票代码（如 sh.600519）。
        start_date : str
            起始日期 YYYY-MM-DD。
        end_date : str
            结束日期 YYYY-MM-DD。
        frequency : str
            频率，默认 "d"（日线）。
        adjustflag : str
            复权标志，"1"=前复权，"2"=后复权，"0"=不复权。

        Returns
        -------
        KlineData | None
            成功返回 KlineData，失败返回 None。
        """
        try:
            self._ensure_import()
            code = self._ticker_to_adk(ticker)
            start_fmt = start_date.replace("-", "")
            end_fmt = end_date.replace("-", "")

            # a-stock-data 的 K 线接口，具体签名请根据实际版本调整
            # 这里假设签名为：adk.stock_hist_daily(code, start_date, end_date, adjust)
            df = self._adk.stock_hist_daily(
                symbol=code,
                start_date=start_fmt,
                end_date=end_fmt,
                adjust=adjustflag,
            )
            if df is None or df.empty:
                return None

            # 尝试常见的列名映射（不同版本可能列名不同）
            col_map = {
                "date": ["date", "日期", "trade_date"],
                "open": ["open", "开盘"],
                "high": ["high", "最高"],
                "low": ["low", "最低"],
                "close": ["close", "收盘"],
                "volume": ["volume", "成交量"],
                "amount": ["amount", "成交额"],
            }

            col_map_inv = {}
            for std_name, candidates in col_map.items():
                for c in candidates:
                    if c in df.columns:
                        col_map_inv[c] = std_name
                        break

            df = df.rename(columns=dict(col_map_inv))

            required = ["timestamps", "open", "high", "low", "close", "volume"]
            missing = [c for c in required if c not in df.columns]
            if missing:
                logger.warning(
                    f"{self.name} K 线缺少必要列: {missing}, 实际列: {df.columns.tolist()}"
                )
                return None

            return KlineData(
                timestamps=pd_to_datetime_safe(df["timestamps"].tolist()),
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

    def fetch_quote(self, ticker: str) -> RealtimeQuote | None:
        """获取实时行情。

        Parameters
        ----------
        ticker : str
            股票代码（如 sh.600519）。

        Returns
        -------
        RealtimeQuote | None
        """
        try:
            self._ensure_import()
            code = self._ticker_to_adk(ticker)

            # a-stock.data 实时行情接口，具体签名请根据实际版本调整
            df = self._adk.stock_zh_a_spot_em(symbol=code)
            if df is None or df.empty:
                return None

            row = df.iloc[0]
            return RealtimeQuote(
                ticker=ticker,
                price=safe_float(row.get("最新价") or row.get("price")),
                pe=safe_float(row.get("市盈率-动态") or row.get("pe_ttm")),
                pb=safe_float(r.get("市净率")) if (r := row) is not None else None,
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
        """获取股票元数据。

        从 a-stock-data 获取股票基本面信息（行业、PE/PB、上市/退市日期等）。

        Parameters
        ----------
        ticker : str
            股票代码（如 sh.600519）。

        Returns
        -------
        StockMetadata | None
        """
        try:
            self._ensure_import()
            code = self._ticker_to_adk(ticker)

            df = self._adk.stock_zh_a_spot_em(symbol=code)
            if df is None or df.empty:
                return None

            row = df.iloc[0]
            industry = str(row.get("行业") or "").strip() or None
            return StockMetadata(
                ticker=ticker,
                industry=industry,
                pe_ttm=safe_float(row.get("市盈率-动态") or row.get("pe_ttm")),
                pb=safe_float(row.get("市净率")),
                source=self.name,
            )
        except ImportError:
            raise
        except Exception as e:
            logger.debug(f"{self.name} 元数据拉取异常 {ticker}: {str(e)[:100]}")
            return None

    def fetch_quotes_batch(
        self,
        tickers: list[str],
        max_workers: int = 8,
    ) -> dict[str, RealtimeQuote]:
        """批量获取实时行情。

        如果 a-stock.data 支持全市场快照，可一次性拉取所有股票；
        否则逐只拉取。

        Parameters
        ----------
        tickers : list[str]
            股票代码列表。
        max_workers : int
            保留参数以兼容基类签名。

        Returns
        -------
        dict[str, RealtimeQuote]
            成功获取的 ticker → 行情映射。
        """
        results: dict[str, RealtimeQuote] = {}
        if not tickers:
            return results

        try:
            self._ensure_import()
            df = self._adk.stock_zh_a_spot_em()
            if df is None or df.empty:
                return results

            code_to_row: dict[str, Any] = {}
            for _, row in df.iterrows():
                code = str(row.get("代码", ""))
                if code:
                    code_to_row[code] = row

            for ticker in tickers:
                code = self._ticker_to_adk(ticker)
                row = code_to_row.get(code)
                if row is None:
                    continue
                try:
                    results[ticker] = RealtimeQuote(
                        ticker=ticker,
                        price=safe_float(row.get("最新价")),
                        pe=safe_float(row.get("市盈率-动态")),
                        pb=safe_float(row.get("市净率")),
                        market_cap=safe_float(row.get("总市值")),
                        turnover=safe_float(row.get("换手率")),
                        source=self.name,
                    )
                except Exception as e:  # noqa: BLE001
                    logger.debug(f"{self.name} 批量行情构建 {ticker} 异常: {e}")
        except ImportError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.debug(f"{self.name} 批量行情拉取异常: {str(e)[:100]}")
        return results

    def fetch_metadata_batch(
        self,
        tickers: list[str],
        max_workers: int = 8,
    ) -> dict[str, StockMetadata]:
        """批量获取股票元数据。

        使用 akshare 的全市场快照缓存模式（如支持），一次拉取全市场后在内存中
        匹配各 ticker，避免逐票 HTTP 请求。

        Parameters
        ----------
        tickers : list[str]
            股票代码列表。
        max_workers : int
            保留参数以兼容基类签名，批量快照模式下不使用多线程。

        Returns
        -------
        dict[str, StockMetadata]
            成功获取的 ticker → 元数据映射。
        """
        results: dict[str, StockMetadata] = {}
        if not tickers:
            return results

        try:
            self._ensure_import()
            df = self._adk.stock_zh_a_spot_em()
            if df is None or df.empty:
                return results

            code_to_row: dict[str, Any] = {}
            for _, row in df.iterrows():
                code = str(row.get("代码", ""))
                if code:
                    code_to_row[code] = row

            for ticker in tickers:
                code = self._ticker_to_adk(ticker)
                row = code_to_row.get(code)
                if row is None:
                    continue
                try:
                    industry = str(row.get("行业") or "").strip() or None
                    results[ticker] = StockMetadata(
                        ticker=ticker,
                        industry=industry,
                        pe_ttm=safe_float(row.get("市盈率-动态")),
                        pb=safe_float(row.get("市净率")),
                        source=self.name,
                    )
                except Exception as e:  # noqa: BLE001
                    logger.debug(f"{self.name} 批量元数据构建 {ticker} 异常: {e}")
        except ImportError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.debug(f"{self.name} 批量元数据拉取异常: {str(e)[:100]}")
        return results

    def health_check(self) -> bool:
        try:
            self._ensure_import()
            df = self._adk.stock_zh_a_hist(
                symbol="600519",
                start_date="20260801",
                end_date="20260813",
                adjust="1",
            )
            return df is not None and not df.empty
        except Exception as e:
            logger.debug(f"{self.name} health check 异常: {e}")
            return False
