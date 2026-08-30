"""
data_providers.tonghuashun_provider — 同花顺（fuyao）金融数据 API Provider。

基于 https://fuyao.aicubes.cn/ 的 REST API，通过 X-api-key 鉴权。
支持：
  · K 线（历史日线，前复权）
  · 实时行情快照（批量）
  · 股票元数据（名称、交易所后缀）

  环境变量（优先读取 HITHINK_FINANCE_API_KEY，兼容 FUYAO_API_KEY）：
  HITHINK_FINANCE_API_KEY — 同花顺 API Key（必填）

API 文档：https://fuyao.aicubes.cn/llms-full.txt
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any, Optional

import requests
from loguru import logger

from trade_krono_cli.data_providers.base import (
    DataProvider,
    KlineData,
    RealtimeQuote,
    StockMetadata,
)

_BASE_URL = "https://fuyao.aicubes.cn"


class TongHuaShunProvider(DataProvider):
    """
    同花顺金融数据 API Provider。

    依赖 HITHINK_FINANCE_API_KEY 环境变量（兼容 FUYAO_API_KEY）。
    支持 K 线、行情快照、元数据三种数据维度。
    """

    name = "tonghuashun"
    supports_kline = True
    supports_quote = True
    supports_metadata = True

    # ── 懒加载 ────────────────────────────────────────────────

    _api_key: str = ""
    _initialized: bool = False

    @classmethod
    def _ensure_init(cls) -> None:
        """延迟初始化：检查 API Key 并缓存。"""
        if cls._initialized:
            return
        key = (
            os.getenv("HITHINK_FINANCE_API_KEY", "").strip()
            or os.getenv("FUYAO_API_KEY", "").strip()
        )
        if not key:
            raise RuntimeError("HITHINK_FINANCE_API_KEY 未配置，请在 .env 中设置同花顺 API Key。")
        cls._api_key = key
        cls._initialized = True

    # ── 内部工具 ──────────────────────────────────────────────

    @staticmethod
    def _headers() -> dict[str, str]:
        return {"X-api-key": TongHuaShunProvider._api_key}

    @staticmethod
    def _get(path: str, params: dict[str, Any] | None = None) -> Optional[dict]:
        """发送 GET 请求，返回 ApiResponse.data 或 None。"""
        try:
            url = f"{_BASE_URL}{path}"
            resp = requests.get(
                url,
                params=params if params is not None else {},
                headers=TongHuaShunProvider._headers(),
                timeout=15,
            )
            resp.raise_for_status()
            body = resp.json()
            if body.get("code") != 0:
                logger.debug(f"ths {path} code={body.get('code')} msg={body.get('message')}")
                return None
            return body.get("data")
        except requests.exceptions.Timeout:
            logger.warning(f"ths 请求超时: {path}")
            return None
        except requests.exceptions.HTTPError as e:
            logger.warning(f"ths HTTP 错误 {path}: {e}")
            return None
        except (ValueError, KeyError) as e:
            logger.debug(f"ths 响应解析失败 {path}: {e}")
            return None

    @staticmethod
    def _date_to_ms(date_str: str) -> int:
        """YYYY-MM-DD → 毫秒时间戳（Asia/Shanghai）。"""
        dt = datetime.strptime(date_str, "%Y-%m-%d")
        return int(dt.timestamp() * 1000)

    @staticmethod
    def _safe_float(value: Any) -> Optional[float]:
        if value is None:
            return None
        try:
            f = float(value)
            if f != f or f == float("inf") or f == float("-inf"):
                return None
            return f
        except (ValueError, TypeError):
            return None

    # ── 核心接口实现 ──────────────────────────────────────────

    def fetch_kline(
        self,
        ticker: str,
        start_date: str,
        end_date: str,
        frequency: str = "d",
        adjustflag: str = "1",
    ) -> Optional[KlineData]:
        """
        拉取单只标的的历史日 K 线（前复权）。

        Parameters
        ----------
        ticker : str
            股票代码，格式 sh.600519 / sz.000858
        start_date : str
            起始日期 YYYY-MM-DD
        end_date : str
            结束日期 YYYY-MM-DD
        frequency : str
            仅支持 "d"
        adjustflag : str
            "1"=前复权（默认），"2"=后复权

        Returns
        -------
        KlineData | None
        """
        try:
            self._ensure_init()
            thscode = self._ticker_to_thscode(ticker)
            if not thscode:
                return None

            adjust = "forward" if adjustflag == "1" else "backward"
            params = {
                "thscode": thscode,
                "interval": "1d",
                "start": self._date_to_ms(start_date),
                "end": self._date_to_ms(end_date),
                "adjust": adjust,
            }
            data = self._get("/api/a-share/prices/historical", params)
            if data is None:
                return None

            items = data.get("item", [])
            if not items:
                return None

            timestamps: list[datetime] = []
            open_list: list[float] = []
            high_list: list[float] = []
            low_list: list[float] = []
            close_list: list[float] = []
            volume_list: list[float] = []
            amount_list: list[float] = []

            for item in items:
                ms = self._safe_float(item.get("date_ms"))
                if ms is None:
                    continue
                timestamps.append(datetime.fromtimestamp(ms / 1000))
                open_list.append(self._safe_float(item.get("open_price")) or 0.0)
                high_list.append(self._safe_float(item.get("high_price")) or 0.0)
                low_list.append(self._safe_float(item.get("low_price")) or 0.0)
                close_list.append(self._safe_float(item.get("close_price")) or 0.0)
                # volume 单位为股
                volume_list.append(self._safe_float(item.get("volume")) or 0.0)
                amount_list.append(self._safe_float(item.get("turnover")) or 0.0)

            return KlineData(
                timestamps=timestamps,
                open=open_list,
                high=high_list,
                low=low_list,
                close=close_list,
                volume=volume_list,
                amount=amount_list,
            )
        except RuntimeError:
            raise
        except Exception as e:
            logger.debug(f"{self.name} K 线拉取异常 {ticker}: {str(e)[:200]}")
            return None

    def fetch_quote(self, ticker: str) -> Optional[RealtimeQuote]:
        """
        获取单只标的的实时行情快照。

        Parameters
        ----------
        ticker : str
            股票代码，格式 sh.600519 / sz.000858

        Returns
        -------
        RealtimeQuote | None
        """
        try:
            self._ensure_init()
            thscode = self._ticker_to_thscode(ticker)
            if not thscode:
                return None

            params = {"thscodes": thscode}
            data = self._get("/api/a-share/prices/snapshot", params)
            if data is None:
                return None

            items = data.get("item", [])
            if not items:
                return None

            item = items[0]
            return RealtimeQuote(
                ticker=ticker,
                price=self._safe_float(item.get("last_price")),
                source=self.name,
            )
        except RuntimeError:
            raise
        except Exception as e:
            logger.debug(f"{self.name} 行情拉取异常 {ticker}: {str(e)[:100]}")
            return None

    def fetch_metadata(self, ticker: str) -> Optional[StockMetadata]:
        """
        获取股票基础元数据（名称、交易所）。
        PE/PB/行业 等字段同花顺 API 不在此端点提供，返回 None。

        Parameters
        ----------
        ticker : str
            股票代码，格式 sh.600519 / sz.000858

        Returns
        -------
        StockMetadata | None
        """
        try:
            self._ensure_init()
            thscode = self._ticker_to_thscode(ticker)
            if not thscode:
                return None

            params = {"q": ticker.split(".")[-1], "limit": 1}
            data = self._get("/api/meta/tickers/search", params)
            if data is None:
                return None

            items = data.get("item", [])
            if not items:
                return None

            return StockMetadata(
                ticker=ticker,
                industry=None,  # ths search 不提供行业
                ipo_date=None,
                out_date=None,
                is_st=False,
                source=self.name,
            )
        except RuntimeError:
            raise
        except Exception as e:
            logger.debug(f"{self.name} 元数据拉取异常 {ticker}: {str(e)[:100]}")
            return None

    def health_check(self) -> bool:
        """尝试拉取贵州茅台的 K 线数据验证连通性。"""
        try:
            self._ensure_init()
            data = self._get(
                "/api/a-share/prices/historical",
                {
                    "thscode": "600519.SH",
                    "interval": "1d",
                    "start": self._date_to_ms("2026-08-01"),
                    "end": self._date_to_ms("2026-08-29"),
                    "adjust": "forward",
                },
            )
            return data is not None and bool(data.get("item"))
        except Exception:
            return False

    # ── 内部转换 ──────────────────────────────────────────────

    @staticmethod
    def _ticker_to_thscode(ticker: str) -> Optional[str]:
        """sh.600519 → 600519.SH / sz.000858 → 000858.SZ"""
        parts = ticker.split(".")
        if len(parts) != 2:
            return None
        prefix, code = parts
        if prefix == "sh":
            return f"{code}.SH"
        if prefix == "sz":
            return f"{code}.SZ"
        if prefix == "bj":
            return f"{code}.BJ"
        return None

    @staticmethod
    def _thscode_to_ticker(thscode: str) -> str:
        """600519.SH → sh.600519 / 000858.SZ → sz.000858"""
        if "." not in thscode:
            return ""
        code, exchange = thscode.rsplit(".", 1)
        prefix = exchange.lower()
        return f"{prefix}.{code}"
