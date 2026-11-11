"""data_providers.tencent_provider — 腾讯财经数据源实现。

数据源：腾讯财经行情（qt.gtimg.cn / web.ifzq.gtimg.cn / proxy.finance.qq.com）。
来源对齐：scripts.a_stock_data SKILL.md（A股全栈数据工具包 V3.10.x）§1.1 / §1.2。

能力：
  - 实时行情：PE / PB / 市值 / 换手率 / 涨跌停 / 成交额，含 is_stale（僵尸报价）检测
  - K 线：日 / 周 / 月前复权（qfq / hfq）+ 1~60 分钟 K 线（m1 / m5 / m15 / m30 / m60，不复权）
  - 覆盖沪深 A 股 / ETF / 沪深指数；不支持北交所（K 线层抛 ``ValueError``，由工厂降级）

量 / 复权口径（与 SKILL.md §1.1 / §1.2 一致，避免量价口径混用）：
  - 成交量单位：科创板（688 / 689）为「股」，其余为「手」
  - 日 / 周 / 月线默认前复权（qfq）；qfq 为等差口径，高分红老股可能出现 ≤0 价格，检测后拒绝
  - 分钟线仅不复权，单次最多 320 根；日 / 周 / 月单段最多 640 根
  - 本接口不返回成交额，成交额按收盘价 × 成交量换算
"""

from __future__ import annotations

import json
import math
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from typing import Any

from loguru import logger

from trade_krono_cli.data_providers.base import (
    DataProvider,
    KlineData,
    RealtimeQuote,
    StockMetadata,
)
from trade_krono_cli.utils import pd_to_datetime_safe

_TENCENT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
_TENCENT_KLINE_HOSTS: tuple[str, ...] = (
    "https://web.ifzq.gtimg.cn",
    "https://proxy.finance.qq.com/ifzqgtimg",
    "https://ifzq.gtimg.cn",
)
_TENCENT_HOST_COOLDOWN_SEC = 120.0
_TENTINEL_MINUTES: frozenset[str] = frozenset({"m1", "m5", "m15", "m30", "m60"})
_TENTINEL_SPAN_DAYS: dict[str, int] = {"day": 700, "week": 3650, "month": 18250}
_SH_INDEX: frozenset[str] = frozenset({"000300", "000905", "000016", "000688", "000852", "000010"})
_PERIOD_MAP: dict[str, str] = {
    "d": "day",
    "day": "day",
    "w": "week",
    "week": "week",
    "m": "month",
    "month": "month",
    "1min": "m1",
    "2min": "m2",
    "3min": "m3",
    "5min": "m5",
    "15min": "m15",
    "30min": "m30",
    "60min": "m60",
}


def _tencent_http_get(url: str, params: dict[str, str] | None = None) -> str:
    """GET 请求腾讯财经接口并返回响应文本（默认 GBK）。

    Args:
        url: 接口基础 URL（不含 query）。
        params: 可选 query 参数。

    Returns:
        响应文本。

    Raises:
        RuntimeError: 请求失败（网络 / 协议错误）时抛出。
    """
    target = url
    if params:
        target = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(  # noqa: S310
        target,
        headers={"User-Agent": _TENCENT_USER_AGENT},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310
            charset = response.headers.get_content_charset() or "gbk"
            return response.read().decode(charset)
    except Exception as exc:  # noqa: BLE001 — 数据源侧错误统一由调用方降级
        raise RuntimeError(f"tencent 请求 {target} 失败: {type(exc).__name__}") from exc


def _tencent_num(value: Any) -> float | None:
    """把来源字段安全转为 float；空 / 布尔 / 非数字返回 None。

    Args:
        value: 来源字段原始值。

    Returns:
        数值，不可用时返回 None。
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = str(value).replace(",", "").strip()
        if text in ("", "-", "--"):
            return None
        try:
            number = float(text)
        except ValueError:
            return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _tencent_req_num(value: Any, what: str) -> float:
    """必填数值（价格 / 成交量等）：不可用时抛 ``RuntimeError``（源格式异常，不归为参数错）。"""
    number = _tencent_num(value)
    if number is None:
        raise RuntimeError(f"tencent 来源字段 {what} 为空或非数字: {value!r}")
    return number


def _tencent_src_date(value: Any) -> str:
    """来源返回的日期 → ``YYYY-MM-DD``。认不出即抛 ``RuntimeError``。"""
    text = str(value).strip()
    fmt = "%Y%m%d" if re.fullmatch(r"[0-9]{8}", text) else "%Y-%m-%d"
    try:
        return datetime.strptime(text, fmt).strftime("%Y-%m-%d")
    except ValueError as exc:
        raise RuntimeError(f"tencent 来源返回无法识别的日期: {value!r}") from exc


class TencentProvider(DataProvider):
    """腾讯财经数据源实现（实时行情 + K 线 + 估值/市值/换手字段）。"""

    name: str = "tencent"
    supports_kline: bool = True
    supports_quote: bool = True
    supports_metadata: bool = False

    def __init__(self) -> None:
        """初始化腾讯 Provider（含来源熔断冷却状态）。"""
        super().__init__()
        self._host_down_until: dict[str, float] = {}
        self._host_cooldown_sec: float = _TENCENT_HOST_COOLDOWN_SEC

    # ── K 线 ───────────────────────────────────────────────────────────

    def fetch_kline(
        self,
        ticker: str,
        start_date: str,
        end_date: str,
        frequency: str = "d",
        adjustflag: str = "1",
    ) -> KlineData | None:
        """拉取腾讯 K 线。

        Args:
            ticker: 股票代码（已归一化，如 ``sh.600519``）。
            start_date: 起始日期 ``YYYY-MM-DD``。
            end_date: 结束日期 ``YYYY-MM-DD``。
            frequency: 频率，如 ``d`` / ``w`` / ``m`` / ``5min`` / ``15min`` 等。
            adjustflag: 复权口径，``"1"`` 前复权 / ``"2"`` 后复权 / ``"0"`` 无复权。

        Returns:
            KlineData 或 None（频率不支持 / 拉取失败时）。

        Raises:
            ValueError: 北交所代码或复权口径不支持时。
        """
        period = _PERIOD_MAP.get((frequency or "d").strip().lower())
        if period is None:
            logger.debug(f"tencent: 不支持的 K 线频率 {frequency}，跳过")
            return None
        if period in _TENTINEL_MINUTES:
            return self._fetch_tencent_minutes(ticker, period)
        return self._fetch_tencent_daily(
            ticker, period, str(adjustflag or "1"), start_date, end_date
        )

    def _fetch_tencent_daily(
        self, ticker: str, period: str, adjustflag: str, start_date: str, end_date: str
    ) -> KlineData | None:
        """拉取日 / 周 / 月 K 线（前复权 / 后复权 / 无复权，含分页）。

        Args:
            ticker: 股票代码（已归一化，如 ``sh.600519``）。
            period: K 线周期（``day`` / ``week`` / ``month``）。
            adjustflag: 复权口径（``"0"`` 无复权 / ``"1"`` 前复权 / ``"2"`` 后复权）。
            start_date: 起始日期 ``YYYY-MM-DD``。
            end_date: 结束日期 ``YYYY-MM-DD``。

        Returns:
            KlineData 或 None（拉取失败时由调用方降级）。

        Raises:
            ValueError: 北交所代码或复权口径不支持时。
        """
        if adjustflag not in ("0", "1", "2"):
            raise ValueError(f"tencent: 不支持的复权口径 {adjustflag}")
        adjust = {"1": "qfq", "2": "hfq", "0": ""}.get(adjustflag, "")
        symbol = self._to_tencent_symbol(ticker)
        if symbol[:2] == "bj":
            raise ValueError("tencent: 不支持北交所 K 线，请改用 tonghuashun")
        first = _tencent_src_date(start_date) if start_date else None
        last = _tencent_src_date(end_date) if end_date else datetime.now().strftime("%Y-%m-%d")
        windows: list[tuple[str, str, int]] = []
        if first and last:
            first_d = datetime.strptime(first, "%Y-%m-%d").date()
            last_d = datetime.strptime(last, "%Y-%m-%d").date()
            if first_d > last_d:
                raise ValueError(f"tencent: 起始日期 {first} 晚于结束日期 {last}")
            span = _TENTINEL_SPAN_DAYS.get(period, 700)
            cursor = first_d
            while cursor <= last_d:
                stop_d = min(cursor + timedelta(days=span - 1), last_d)
                windows.append((cursor.strftime("%Y-%m-%d"), stop_d.strftime("%Y-%m-%d"), 640))
                cursor = stop_d + timedelta(days=1)
        else:
            windows.append(("", "", 320))
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for window_start, window_end, size in windows:
            data = self._tencent_kline_call(
                "/appstock/app/fqkline/get",
                f"{symbol},{period},{window_start},{window_end},{size},{adjust}",
            )
            node = data.get(symbol)
            key = f"{adjust}{period}" if adjust else period
            items = (
                node.get(key)
                if isinstance(node, dict) and key in node
                else node.get(period)
                if isinstance(node, dict)
                else None
            )
            if not isinstance(items, list):
                raise RuntimeError(f"tencent K 线 {symbol} {period} 返回格式异常")
            for item in items:
                row_date = _tencent_src_date(item[0])
                if window_start and not window_start <= row_date <= window_end:
                    raise RuntimeError(
                        f"tencent K 线 {symbol} 请求 {window_start}~{window_end} 返回 {row_date}"
                    )
                if row_date in seen:
                    raise RuntimeError(f"tencent K 线 {symbol} 日期 {row_date} 重复")
                seen.add(row_date)
                rows.append(
                    {
                        "timestamp": datetime.strptime(row_date, "%Y-%m-%d"),
                        "open": _tencent_req_num(item[1], "open"),
                        "high": _tencent_req_num(item[3], "high"),
                        "low": _tencent_req_num(item[4], "low"),
                        "close": _tencent_req_num(item[2], "close"),
                        "volume": _tencent_req_num(item[5], "volume"),
                    }
                )
        if not rows:
            raise RuntimeError(f"tencent K 线 {symbol} {period} 在所给区间内 0 根")
        if any(min(r["open"], r["high"], r["low"], r["close"]) <= 0 for r in rows):
            raise RuntimeError(f"tencent K 线 {symbol} {period} 价格出现 ≤0（等差复权口径副作用）")
        kline = self._assemble_kline(symbol, rows)
        return kline

    def _fetch_tencent_minutes(self, ticker: str, period: str) -> KlineData | None:
        """拉取分钟 K 线（m1/m5/m15/m30/m60，仅不复权，count ≤ 320）。"""
        symbol = self._to_tencent_symbol(ticker)
        data = self._tencent_kline_call("/appstock/app/kline/mkline", f"{symbol},{period},,,320")
        node = data.get(symbol)
        items = node.get(period) if isinstance(node, dict) else None
        if not isinstance(items, list) or not items:
            raise RuntimeError(f"tencent 分钟线 {symbol} {period} 返回 0 根")
        rows: list[dict[str, Any]] = []
        seen: set[datetime] = set()
        for item in items:
            stamp = datetime.strptime(str(item[0]), "%Y%m%d%H%M")
            if stamp in seen:
                raise RuntimeError(f"tencent 分钟线 {symbol} 时刻 {stamp} 重复")
            seen.add(stamp)
            rows.append(
                {
                    "timestamp": stamp,
                    "open": _tencent_req_num(item[1], "open"),
                    "high": _tencent_req_num(item[3], "high"),
                    "low": _tencent_req_num(item[4], "low"),
                    "close": _tencent_req_num(item[2], "close"),
                    "volume": _tencent_req_num(item[5], "volume"),
                    "turnover_rate_pct": _tencent_num(item[7]),
                }
            )
        return self._assemble_kline(symbol, rows)

    def _tencent_kline_call(self, path: str, param: str) -> dict[str, Any]:
        """按三个入口顺序拉 K 线，单入口失败后冷却 120s 再换下一个。

        Args:
            path: 接口路径（如 ``/appstock/app/kline/mkline``）。
            param: 接口参数字符串。

        Returns:
            ``{"code": 0, "data": {...}}`` 风格的 dict。

        Raises:
            RuntimeError: 全部入口均不可用时。
            ValueError: 来源返回 ``param error`` 时。
        """
        errors: list[str] = []
        for host in _TENCENT_KLINE_HOSTS:
            if self._host_down_until.get(host, 0.0) > time.time():
                continue
            url = f"{host}{path}"
            try:
                payload = json.loads(_tencent_http_get(url, {"param": param}))
            except Exception as exc:  # noqa: BLE001
                errors.append(host)
                self._host_down_until[host] = time.time() + self._host_cooldown_sec
                logger.debug(f"tencent K 线 {host}{path} 失败: {exc}")
                continue
            if not isinstance(payload, dict):
                errors.append(host)
                self._host_down_until[host] = time.time() + self._host_cooldown_sec
                continue
            if payload.get("msg") == "param error":
                raise ValueError(f"tencent K 线参数错误（区间过长或代码不存在）: {param}")
            data = payload.get("data")
            if isinstance(data, dict) and data:
                return data
            errors.append(host)
            self._host_down_until[host] = time.time() + self._host_cooldown_sec
        raise RuntimeError(f"tencent K 线全部入口不可用: {'; '.join(errors)}")

    # ── 行情 ────────────────────────────────────────────────────────────

    def fetch_quote(self, ticker: str) -> RealtimeQuote | None:
        """获取腾讯实时行情（PE / PB / 市值 / 换手率 / 涨跌停 / 成交额）。

        Args:
            ticker: 股票代码（如 ``sh.600519``）。

        Returns:
            RealtimeQuote 或 None。
        """
        prefixed = self._to_tencent_symbol(ticker)
        url = f"https://qt.gtimg.cn/q={prefixed}"
        data = _tencent_http_get(url, None)
        quote: dict[str, Any] | None = None
        for line in data.strip().split(";"):
            stripped = line.strip().rstrip(";").strip()
            if not stripped or "=" not in stripped or '"' not in stripped:
                continue
            key = stripped.split("=", 1)[0].split("_")[-1]
            if key != prefixed:
                continue
            parts = stripped.split('"')
            if len(parts) < 2:
                continue
            value = parts[1]
            vals = value.split("~")
            if len(vals) < 53:
                continue
            price = _tencent_num(vals[3]) or 0.0
            last_close = _tencent_num(vals[4]) or 0.0
            amount_wan = _tencent_num(vals[37]) or 0.0
            quote = {
                "ticker": ticker,
                "name": vals[1],
                "price": price,
                "last_close": last_close,
                "amount_wan": amount_wan,
                "pe": _tencent_num(vals[39]),
                "pb": _tencent_num(vals[46]),
                "market_cap": _tencent_num(vals[45]),
                "turnover": _tencent_num(vals[38]),
                "is_stale": amount_wan == 0 and price > 0 and price == last_close,
            }
            break
        if quote is None:
            return None
        return RealtimeQuote(
            ticker=ticker,
            price=quote["price"],
            pe=quote["pe"],
            pb=quote["pb"],
            market_cap=quote["market_cap"],
            turnover=quote["turnover"],
            source=self.name,
        )

    def fetch_metadata(self, ticker: str) -> StockMetadata | None:
        """腾讯不提供完整基本面元数据，返回 None。"""
        return None

    def health_check(self) -> bool:
        """检查腾讯数据源健康状态（拉取一只股票的实时行情）。"""
        try:
            return self.fetch_quote("sh.600519") is not None
        except Exception as exc:  # noqa: BLE001
            logger.debug(f"tencent health check 异常: {exc}")
            return False

    # ── 内部工具 ─────────────────────────────────────────────────────────

    def _assemble_kline(self, symbol: str, rows: list[dict[str, Any]]) -> KlineData:
        """把行字典组装为 KlineData（含成交额换算）。

        Args:
            symbol: tencent 规范化代码（如 ``sh600519``），用于成交量单位换算。
            rows: 行字典列表（``timestamp / open / high / low / close / volume``）。

        Returns:
            KlineData。
        """
        timestamps: list[datetime] = []
        opens: list[float] = []
        highs: list[float] = []
        lows: list[float] = []
        closes: list[float] = []
        volumes: list[float] = []
        amounts: list[float] = []
        for row in rows:
            close = row["close"]
            volume = row["volume"]
            is_csj = symbol[2:].startswith(("688", "689"))
            shares = volume * (1.0 if is_csj else 100.0)
            timestamps.append(row["timestamp"])
            opens.append(row["open"])
            highs.append(row["high"])
            lows.append(row["low"])
            closes.append(close)
            volumes.append(volume)
            amounts.append(close * shares)
        return KlineData(
            timestamps=pd_to_datetime_safe(timestamps),
            open=opens,
            high=highs,
            low=lows,
            close=closes,
            volume=volumes,
            amount=amounts,
        )

    def _to_tencent_symbol(self, ticker: str) -> str:
        """归一化 ticker 为腾讯代码格式（``sh600519`` / ``sz000858`` / ``bj920080``）。

        Args:
            ticker: 归一化 ticker（如 ``sh.600519``、``sz.000858``、``bj.920080``）。

        Returns:
            Tencent 代码字符串。

        Raises:
            ValueError: 无法解析为 6 位数字代码时。
        """
        code = ticker.strip()
        prefix = "sh"
        bare = code
        if "." in code:
            p, bare = code.split(".", 1)
            if p.lower() in ("sh", "sz", "bj"):
                prefix = p.lower()
        bare = bare.strip()
        if not bare.isdigit() or len(bare) != 6:
            raise ValueError(f"tencent: 无法解析的股票代码 {ticker}")
        if bare.startswith(("15", "16", "18")) or bare.startswith(("0", "3")):
            # 深市代码段（0 始深证主板 / 3 始创业板 / 15/16/18 深证 ETF）→ sz
            prefix = "sz"
        return f"{prefix}{bare}"
