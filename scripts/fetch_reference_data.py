#!/usr/bin/env python3
"""批量拉取股票元数据（参考数据）。

通过 akshare 批量获取所有 A 股名称、PE/PB 等基础信息（单次 HTTP 请求，
约 77 秒获取全量 5900+ 只），避免逐个调用 baostock/tonghuashun 导致
会话卡死。

写入 outputs/cache/metadata.json，供分析脚本使用。

支持断点续传（checkpoint）：
  全量模式每批完成后将进度写入 outputs/cache/metadata_checkpoint.json，
  中断后再次运行自动从 checkpoint 续拉（跳过已成功、重试失败/None）。

用法：
  uv run python scripts/fetch_reference_data.py
  uv run python scripts/fetch_reference_data.py --tickers "000001,002027"
  uv run python scripts/fetch_reference_data.py --force-refresh
  uv run python scripts/fetch_reference_data.py --force-refresh --resume
  uv run python scripts/fetch_reference_data.py --reset-checkpoint
"""

from __future__ import annotations

import json
import os
import ssl
import sys
import time
import urllib.parse
import urllib.request
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import TypedDict, cast

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger

from scripts._utils import (
    CACHE_DB,
    get_all_tickers,
    get_sqlite_conn,
    is_etf_ticker,
    ticker_to_thscode,
)
from scripts.stock_name_cache import (
    get_missing_name_tickers,
    refresh_missing_stock_names,
    seed_stock_name_cache_from_map,
)
from trade_krono_cli.cli_commands.core import _load_env
from trade_krono_cli.data_providers.factory import (
    get_data_factory,  # noqa: F401  # 测试通过 monkeypatch 使用
)
from trade_krono_cli.data_providers.rate_limiter import get_rate_limiter

FETCH_PER_TICKER_TIMEOUT = 8  # 单只元数据拉取超时：8s 后快速放弃

# 同花顺 Fuyao API（用于 A 股股息率补计算：分红总额 / (PE_TTM × 净利润) × 100%）
_FUYAO_BASE = "https://fuyao.aicubes.cn"


def _get_fuyao_api_key() -> str:
    """动态读取 Fuyao API Key（调用时读取，避免模块 import 时 .env 尚未加载）。

    优先读 HITHINK_FINANCE_API_KEY，兼容 FUYAO_API_KEY。

    Returns:
        非空的 API Key 字符串；未配置时返回空字符串。
    """
    return (
        os.getenv("HITHINK_FINANCE_API_KEY", "").strip()
        or os.getenv("FUYAO_API_KEY", "").strip()
    )


def _get_metadata_file() -> Path:
    """获取 metadata.json 路径（兼容测试隔离，与 get_cache_db_path 同模式）。"""
    try:
        from trade_krono_cli.config import get_settings  # noqa: PLC0415

        settings = get_settings()
        return Path(settings.cache_dir) / "metadata.json"
    except Exception as e:
        logger.warning(f"⚠️  获取 metadata 路径失败: {e}，使用默认路径")
        return Path("outputs/cache/metadata.json")


METADATA_FILE = _get_metadata_file()
CHECKPOINT_FILE = Path("outputs/cache/metadata_checkpoint.json")


class _StockMetadata(TypedDict, total=False):
    """单只股票元数据的字段契约（TypedDict）。

    所有字段均可选（total=False），因为不同数据源/阶段会逐步填充：
    - akshare 批量接口提供 ticker/name/pe_ttm/pb/dividend_yield
    - stock_individual_info_em 补全 industry/market_cap/ipo_date/is_st
    - Fuyao API 补全 dividend_yield + dividend_yield_source
    """

    ticker: str
    name: str
    industry: str | None
    pe_ttm: float | None
    pb: float | None
    dividend_yield: float | None
    market_cap: float | None
    ipo_date: str | None
    out_date: str | None
    is_st: bool
    source: str
    dividend_yield_source: str | None


def _normalize_ticker_row(
    code: str,
    name: str,
    pe_ttm_raw: object,
    pb_raw: object,
    *,
    source: str,
    dividend_yield_raw: object | None = None,
    industry: str | None = None,
    market_cap: float | None = None,
    ipo_date: str | None = None,
    out_date: str | None = None,
    is_st: bool = False,
) -> _StockMetadata | None:
    """将单只股票的原始行归一化为标准元数据 dict。

    负责添加交易所前缀（sh./sz.）、清洗数值字段、补全默认字段。
    北交所及非 6/0/3 开头代码将返回 None，调用方应跳过。

    Args:
        code: 6 位裸股票代码。
        name: 股票名称。
        pe_ttm_raw: PE_TTM 原始值。
        pb_raw: PB 原始值。
        source: 数据源标识，如 "akshare" / "eastmoney"。
        dividend_yield_raw: 股息率原始值，未提供时为 None。
        industry: 行业，未提供时为 None。
        market_cap: 总市值，未提供时为 None。
        ipo_date: 上市日期，未提供时为 None。
        out_date: 退市日期，未提供时为 None。
        is_st: 是否 ST，默认 False。

    Returns:
        标准化的元数据 dict；北交所/其他前缀时返回 None。
    """
    if not code:
        return None

    if code.startswith("6"):
        ticker = f"sh.{code}"
    elif code.startswith("0") or code.startswith("3"):
        ticker = f"sz.{code}"
    else:
        # 北交所等其他前缀，暂不处理
        return None

    def _to_float(v: object) -> float | None:
        if v in (None, "", "-", "None"):
            return None
        try:
            return float(str(v))
        except (ValueError, TypeError):
            return None

    return {
        "ticker": ticker,
        "name": name,
        "industry": industry,
        "pe_ttm": _to_float(pe_ttm_raw),
        "pb": _to_float(pb_raw),
        "dividend_yield": _to_float(dividend_yield_raw),
        "market_cap": market_cap,
        "ipo_date": ipo_date,
        "out_date": out_date,
        "is_st": is_st,
        "source": source,
    }


# ════════════════════════════════════════════════════════════════════════════════
# akshare 批量获取股票元数据（名称 + PE/PB + 总市值 + 行业 + 上市日期）
# ════════════════════════════════════════════════════════════════════════════════


def _fetch_akshare_all_metadata() -> dict[str, _StockMetadata]:
    """用 akshare 批量获取所有 A 股的名称、PE/PB、总市值、行业、上市日期。

    单次 HTTP 请求即可获取全量 A 股行情（约 5900 只），耗时约 77 秒，
    避免逐个调用 provider 导致 baostock/tonghuashun 会话卡死。
    行业、总市值、上市日期通过并行调用 stock_individual_info_em 补全。

    Returns:
        {归一化 ticker: metadata dict}，失败时返回空 dict。
    """
    try:
        import akshare as ak  # noqa: PLC0415

        # 1) 批量获取行情（名称、PE/PB、股息率）
        df = ak.stock_zh_a_spot_em()
        logger.info(f"✅ akshare 批量获取股票行情: {len(df)} 只")

        base_meta: dict[str, _StockMetadata] = {}

        for _, row in df.iterrows():
            code = str(row["代码"]).strip()
            name = str(row["名称"]).strip()
            meta = _normalize_ticker_row(
                code,
                name,
                row.get("市盈率-动态"),
                row.get("市净率"),
                source="akshare",
                dividend_yield_raw=row.get("股息率") or row.get("股息率TTM"),
            )
            if meta is None:
                continue
            base_meta[meta["ticker"]] = meta

        logger.info(f"✅ akshare 基础元数据映射完成: {len(base_meta)} 只")

        # 2) 并行获取行业、总市值、上市日期（stock_individual_info_em）
        # 仅对 base_meta 中的 ticker 并行补全
        enriched = _enrich_metadata_parallel(base_meta)

        logger.info(f"✅ akshare 元数据并行补全完成: {len(enriched)} 只")
        return enriched

    except Exception as e:
        logger.warning(f"⚠️  akshare 批量获取元数据失败: {e}，尝试 urllib 降级直连 push2.eastmoney.com")
        return _fetch_metadata_urllib()


def _enrich_metadata_parallel(base_meta: dict[str, _StockMetadata]) -> dict[str, _StockMetadata]:
    """并行调用 stock_individual_info_em 补全行业、总市值、上市日期。

    使用 ThreadPoolExecutor 并发，避免串行请求过慢。

    Args:
        base_meta: {ticker: 基础元数据}

    Returns:
        补全后的 {ticker: 完整元数据}
    """
    import akshare as ak  # noqa: PLC0415 - 函数内延迟导入，避免模块 import 时加载重型依赖

    tickers = list(base_meta.keys())
    total = len(tickers)
    logger.info(f"🔄 并行补全行业/总市值/上市日期: {total} 只，worker=20...")

    def _fetch_one(ticker: str) -> tuple[str, dict | None]:
        """获取单只股票的详细信息。"""
        code = ticker.split(".", 1)[1]  # sh.600519 -> 600519
        try:
            df = ak.stock_individual_info_em(symbol=code)
            if df is None or len(df) == 0:
                return ticker, None
            # df 列: item, value
            info = dict(zip(df["item"], df["value"], strict=True))
            return ticker, {
                "industry": str(info.get("行业", "")).strip() or None,
                "market_cap": float(info.get("总市值", 0)) if info.get("总市值") not in (None, "", "-") else None,
                "ipo_date": str(info.get("上市时间", "")).strip() or None,
            }
        except Exception as e:
            logger.debug(f"{ticker} 详细信息获取失败: {e}")
            return ticker, None

    enriched: dict[str, _StockMetadata] = {}
    completed = 0
    failed = 0

    with ThreadPoolExecutor(max_workers=20) as executor:
        futures = {executor.submit(_fetch_one, t): t for t in tickers}
        for fut in as_completed(futures):
            ticker, detail = fut.result()
            base = base_meta.get(ticker, {})
            if detail:
                # 合并基础字段 + 补全字段
                merged = cast("_StockMetadata", {**base, **detail})
                # 标记 ST（名称含 ST/*ST/退市）
                name = base.get("name", "")
                merged["is_st"] = any(kw in name for kw in ("ST", "*ST", "退市"))
                enriched[ticker] = merged
            else:
                # 失败时保留基础字段，补全字段置 None
                enriched[ticker] = {
                    **base,
                    "industry": None,
                    "market_cap": None,
                    "ipo_date": None,
                    "is_st": any(kw in base.get("name", "") for kw in ("ST", "*ST", "退市")),
                }
                failed += 1
            completed += 1
            if completed % 500 == 0 or completed == total:
                logger.info(f"  进度: {completed}/{total} (失败 {failed})")

    return enriched


def _fetch_metadata_urllib() -> dict[str, _StockMetadata]:
    """用 urllib 直连东方财富 push2 接口，获取全量 A 股名称、PE/PB、总市值。

    当 akshare 的 requests 客户端被远程服务器拦截（RemoteDisconnected）时，
    作为降级方案。使用 push2.eastmoney.com（而非 82.push2.eastmoney.com），
    该域名在当前网络环境下可直连。

    分页拉取（pz=100），每页间隔短暂休眠避免频率限制，直到全部
    total 只股票获取完毕。

    Returns:
        {归一化 ticker: metadata dict}，失败时返回空 dict。
    """
    base = "https://push2.eastmoney.com/api/qt/clist/get"
    base_params = {
        "po": "1",
        "np": "1",
        "ut": "bd1d9ddb04089700cf9c27f6f7426281",
        "fltt": "2",
        "invt": "2",
        "fid": "f12",
        "fs": "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23",
        "fields": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f13,f14,f15,f16,f17,f18,f20,f21,f23,f24,f25,f22,f11,f62,f128,f136,f115,f152",
    }
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
        ),
        "Accept": "*/*",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": "https://quote.eastmoney.com/",
    }
    ctx = ssl.create_default_context()

    result: dict[str, _StockMetadata] = {}
    pn = 1
    max_retries = 3
    while True:
        params = dict(base_params)
        params["pn"] = str(pn)
        params["pz"] = "100"
        url = base + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url, headers=headers)  # noqa: S310 - 仅访问配置中的可信 HTTP API

        fetched = False
        for attempt in range(max_retries):
            try:
                with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:  # noqa: S310 - URL 来源受控
                    data = json.loads(resp.read().decode("utf-8"))
                fetched = True
                break
            except Exception as e:
                if attempt < max_retries - 1:
                    logger.debug(f"⚠️  urllib 第 {pn} 页第 {attempt + 1} 次尝试失败: {e}，重试中")
                    time.sleep(1.0 * (attempt + 1))
                else:
                    logger.warning(f"⚠️  urllib 第 {pn} 页多次重试后仍失败: {e}")
                    break

        if not fetched:
            break

        diff = data.get("data", {}).get("diff", [])
        if not diff:
            break

        for row in diff:
            code = str(row.get("f12", "")).strip()
            name = str(row.get("f14", "")).strip()
            meta = _normalize_ticker_row(
                code,
                name,
                row.get("f9"),
                row.get("f23"),
                source="eastmoney",
                dividend_yield_raw=None,
                industry=None,
                ipo_date=None,
                out_date=None,
                is_st=False,
            )
            if meta is None:
                continue
            result[meta["ticker"]] = meta

        total = data.get("data", {}).get("total", 0)
        if len(result) >= total:
            break
        pn += 1
        # 同 akshare：分页间短暂休眠，避免触发东方财富频率限制
        time.sleep(0.6)

    logger.info(f"✅ urllib 元数据映射完成: {len(result)} 只")
    return result




# ════════════════════════════════════════════════════════════════════════════════
# 同花顺 Fuyao API：A 股股息率补计算（分红总额 / (PE_TTM × 净利润) × 100%）
# ════════════════════════════════════════════════════════════════════════════════


def _safe_float(v: object) -> float | None:
    """将任意值安全转换为 float；None/NaN/Inf/非数字均返回 None。

    Args:
        v: 待转换的值。

    Returns:
        转换后的浮点数，无法转换时返回 None。
    """
    if v is None:
        return None
    try:
        f = float(str(v))
        if f != f or abs(f) == float("inf"):
            return None
        return f
    except (ValueError, TypeError):
        return None


def _fuyao_api_get(
    path: str, params: dict[str, str] | None = None, timeout: int = 8
) -> dict | None:
    """通过 urllib 向同花顺 Fuyao API 发 GET 请求并返回解析后的 JSON。

    不使用 curl 子进程，避免额外进程开销；所有请求均携带 X-api-key 认证头。
    每次调用前通过 RateLimiter 限流（provider="fuyao"），避免多 worker 并发
    时 HTTP 实际频率超过配置值（默认 2 请求/秒）。

    Args:
        path: API 路径，如 ``/api/a-share/financials/cash-flow-statements``。
        params: 查询参数，如 ``{"thscode": "600519.SH", "period": "annual", "limit": "1"}``。
        timeout: 请求超时秒数。

    Returns:
        解析后的 JSON dict；未配置 API Key、请求失败或非 200 响应时返回 None。
    """
    api_key = _get_fuyao_api_key()
    if not api_key:
        return None
    # 每次 HTTP 请求前限流，保证实际频率不超过 RateLimiter 配置值
    limiter = get_rate_limiter()
    wait = limiter.acquire(provider="fuyao")
    if wait > 0:
        time.sleep(wait)
    url = _FUYAO_BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(  # noqa: S310 - 仅访问配置中的可信 HTTP API
        url,
        headers={
            "X-api-key": api_key,
            "User-Agent": "trade-krono-cli/1.0",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - URL 来源受控
            body = resp.read().decode("utf-8")
        data: object = json.loads(body)
        if not isinstance(data, dict):
            return None
        return data
    except Exception:
        return None


def _fuyao_first_number(
    path: str, params: dict[str, str] | None, field: str
) -> float | None:
    """通过 Fuyao API 获取单数值字段。

    调用指定接口，取返回 ``data.item[0]`` 中的字段并安全转为 float；
    接口失败、code 非 0、字段缺失或数值非正数均返回 None。

    Args:
        path: API 路径，如 ``/api/a-share/financials/cash-flow-statements``。
        params: 查询参数。
        field: 要提取的字段名，如 ``pay_dividends_profits_interest_cash``。

    Returns:
        字段的 float 值，获取失败或非正数时返回 None。
    """
    data = _fuyao_api_get(path, params)
    if data is None or data.get("code") != 0:
        return None
    items = data.get("data", {}).get("item", [])
    if not isinstance(items, list) or not items:
        return None
    value = _safe_float(items[0].get(field))
    if value is None or value <= 0:
        return None
    return value


def _calc_dividend_yield_for_ticker(ticker: str) -> float | None:
    """计算单只 A 股的股息率（%）。

    公式：股息率 = 分红总额 / (PE_TTM × 净利润) × 100%
    其中 分红总额 来自现金流量表，净利润 来自利润表，PE_TTM 来自估值快照。
    三接口均需成功返回且数值大于 0，否则返回 None。

    Args:
        ticker: 带交易所前缀的 A 股 ticker，如 ``sh.600519``。

    Returns:
        股息率百分比（%），无法计算时返回 None。
    """
    thscode = ticker_to_thscode(ticker)
    if not thscode or "." not in ticker:
        return None

    pay_div = _fuyao_first_number(
        "/api/a-share/financials/cash-flow-statements",
        {"thscode": thscode, "period": "annual", "limit": "1"},
        "pay_dividends_profits_interest_cash",
    )
    net_profit = _fuyao_first_number(
        "/api/a-share/financials/income-statements",
        {"thscode": thscode, "period": "annual", "limit": "1"},
        "parent_holder_net_profit",
    )
    pe_ttm = _fuyao_first_number(
        "/api/a-share/valuations/snapshot",
        {"thscodes": thscode},
        "pe_ttm",
    )
    if pay_div is None or net_profit is None or pe_ttm is None:
        return None

    # 股息率 = 分红总额 / (PE_TTM × 净利润) × 100%
    # 推导：股价 × 总股本 = PE_TTM × EPS × (净利润 / EPS) = PE_TTM × 净利润
    # 注意：净利润取最近一个完整年报，PE_TTM 为滚动 12 个月，
    # 年报未披露期（如 1–4 月）两者存在最多约一个季度的口径偏差。
    return (pay_div / (pe_ttm * net_profit)) * 100


def _calc_dividend_yields_batch(
    results: dict[str, _StockMetadata | None],
    tickers: list[str],
) -> None:
    """批量计算并填充缺失股息率的 A 股元数据。

    仅对 ``dividend_yield is None`` 的 ticker 调用 Fuyao API 补计算；
    未配置 API Key 时直接 warning 返回，不做任何网络请求。
    使用 ThreadPoolExecutor 并发拉取（4 worker），每次 HTTP 请求在
    ``_fuyao_api_get`` 内部通过 RateLimiter 限流（每秒 2 请求），
    避免多 worker 并发时实际频率超过配置值。

    Args:
        results: {归一化 ticker: metadata dict 或 None}，原位更新 ``dividend_yield`` 字段。
        tickers: 需要尝试计算股息率的 ticker 列表（已过滤 bj./hk./ETF）。
    """
    if not _get_fuyao_api_key():
        logger.warning("⚠️  未配置 HITHINK_FINANCE_API_KEY/FUYAO_API_KEY，跳过 A 股股息率补计算")
        return

    # 只对 dividend_yield 为 None 的 ticker 计算
    missing: list[str] = []
    for t in tickers:
        m = results.get(t)
        if m is not None and isinstance(m, dict) and m.get("dividend_yield") is None:
            missing.append(t)
    if not missing:
        logger.debug("✅ 所有 ticker 的股息率均已填充，无需补计算")
        return

    logger.info(f"📊 开始 A 股股息率补计算，共 {len(missing)} 只待计算（Fuyao API）")

    success = 0
    failed = 0

    def _calc_one(ticker: str) -> tuple[str, float | None]:
        try:
            dy = _calc_dividend_yield_for_ticker(ticker)
            return (ticker, dy)
        except Exception as e:
            logger.debug(f"⚠️  {ticker} 股息率计算异常: {e}")
            return (ticker, None)

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(_calc_one, t): t for t in missing}
        for i, fut in enumerate(as_completed(futures), 1):
            ticker, dy = fut.result()
            m = results.get(ticker)
            if isinstance(m, dict):
                if dy is not None:
                    m["dividend_yield"] = dy
                    m["dividend_yield_source"] = "fuyao"  # 标记来源，便于审计口径偏差
                    success += 1
                else:
                    failed += 1
            if i % 50 == 0 or i == len(missing):
                logger.info(
                    f"📈 股息率补计算进度: {i}/{len(missing)}（成功 {success}，失败 {failed}）"
                )

    logger.info(f"✅ A 股股息率补计算完成: 成功 {success}，失败 {failed}，共 {len(missing)} 只")


# ════════════════════════════════════════════════════════════════════════════════
# 原有工具函数（断点续传、原子写入等）
# ════════════════════════════════════════════════════════════════════════════════


def _load_existing_metadata() -> dict[str, dict | None]:
    """读取现有 metadata.json；文件不存在或损坏时返回空 dict。

    Returns:
        {归一化 ticker: metadata dict 或 None} 映射
    """
    if not METADATA_FILE.exists():
        return {}
    try:
        data = json.loads(METADATA_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"⚠️ 读取现有 metadata.json 失败，将全量重建: {str(e)[:80]}")
        return {}


# ── 断点续传 checkpoint ──────────────────────────────────────────────────


def load_checkpoint() -> dict[str, dict | None]:
    """读取 checkpoint 文件；不存在或损坏时返回空 dict。

    Returns:
        {归一化 ticker: metadata dict 或 None} 映射
    """
    if not CHECKPOINT_FILE.exists():
        return {}
    try:
        data = json.loads(CHECKPOINT_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"⚠️ 读取 checkpoint 失败，将全量重拉: {str(e)[:80]}")
        return {}


def _atomic_write_json(path: Path, data: Mapping[str, object]) -> None:
    """原子写 JSON：先写同目录临时文件再 rename，避免其它进程读到半截文件。

    Args:
        path: 目标文件路径。
        data: 待写入的 JSON 映射。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def save_checkpoint(results: dict[str, dict | None]) -> None:
    """将当前进度原子写入 checkpoint 文件（先写临时文件再 rename，避免写一半损坏）。

    Args:
        results: {归一化 ticker: metadata dict 或 None} 当前已完成的映射
    """
    _atomic_write_json(CHECKPOINT_FILE, results)


def clear_checkpoint() -> None:
    """删除 checkpoint 文件（全量完成时调用）。"""
    try:
        CHECKPOINT_FILE.unlink(missing_ok=True)
    except OSError as e:
        logger.warning(f"⚠️ 清除 checkpoint 失败: {e}")


def metadata_age_hours() -> float | None:
    """metadata.json 距上次更新的小时数；文件不存在返回 None。"""
    if not METADATA_FILE.exists():
        return None
    return (time.time() - METADATA_FILE.stat().st_mtime) / 3600.0


def refresh_metadata_if_stale(max_age_hours: float = 24.0) -> bool:
    """metadata.json 超过 max_age_hours 未更新时触发全量刷新。

    每日流程第 1 步调用，保证元数据新鲜（默认 24h 未刷新则重拉全量）。

    Args:
        max_age_hours: 刷新阈值（小时），默认 24。

    Returns:
        True 执行了刷新，False 跳过（够新或异常外无副作用）。
    """
    age = metadata_age_hours()
    if age is not None and age < max_age_hours:
        logger.info(f"✅ metadata.json {age:.1f}h 前已更新（<{max_age_hours:.0f}h），跳过刷新")
        return False
    reason = "不存在" if age is None else f"{age:.1f}h 前更新"
    logger.info(f"📊 metadata.json {reason}，开始全量刷新")
    # TTL 触发时若有 checkpoint（上次中断），自动续传；否则全量重拉
    has_checkpoint = load_checkpoint()
    main(tickers_input=None, resume=bool(has_checkpoint))
    return True


def _query_name_baostock(ticker: str) -> str | None:
    """用 baostock 逐只查询股票名称（单只兜底，带 8s 硬超时）。

    仅在本地名称缓存缺失时调用；查询失败/超时返回 None，调用方不写入，
    下次每日流程继续补漏。

    Args:
        ticker: 带交易所前缀的 ticker，如 ``sh.600519``。

    Returns:
        股票名称，查不到时返回 None。
    """
    import threading

    try:
        from trade_krono_cli.data_providers.factory import get_data_factory

        factory = get_data_factory()
        provider = factory.get_provider("baostock")
        if provider is None:
            return None

        holder: dict[str, str | None] = {}

        def _run() -> None:
            try:
                rows = provider._query_stock_basic(  # type: ignore[attr-defined]
                    ticker
                )
                if rows and len(rows[0]) > 1:
                    holder["name"] = rows[0][1].strip()
                else:
                    holder["name"] = None
            except Exception as exc:  # noqa: BLE001
                holder["name"] = None
                logger.debug(f"{ticker} baostock 名称查询异常: {exc}")

        t = threading.Thread(target=_run, daemon=True)
        t.start()
        t.join(timeout=FETCH_PER_TICKER_TIMEOUT)
        if t.is_alive():
            logger.debug(f"{ticker} baostock 名称查询超时({FETCH_PER_TICKER_TIMEOUT}s)，跳过")
            return None
        return holder.get("name")
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"{ticker} baostock 名称查询失败: {exc}")
        return None


def _collect_metadata() -> dict[str, _StockMetadata]:
    """用 akshare 批量获取所有 A 股元数据（名称、PE/PB 等）。

    Returns:
        {ticker: 元数据 dict} 映射
    """
    return _fetch_akshare_all_metadata()


def _seed_name_cache(name_map: dict[str, _StockMetadata]) -> None:
    """将 akshare 批量获取的名称种入本地 SQLite 名称缓存（幂等 upsert）。

    命中即本地使用，不联网；作为后续每日查询的本地种子。

    Args:
        name_map: {ticker: 元数据 dict} 来自 akshare 的元数据映射
    """
    try:
        _conn = get_sqlite_conn(CACHE_DB)
        try:
            name_only: dict[str, str] = {}
            for t, m in name_map.items():
                if isinstance(m, dict) and m.get("name"):
                    name_only[t] = str(m["name"])
            seed_stock_name_cache_from_map(_conn, name_only)
        finally:
            _conn.close()
    except Exception as e:
        logger.debug(f"种入名称缓存失败（不影响元数据主流程）: {e}")


def _filter_tickers(tickers: list[str]) -> list[str]:
    """过滤掉北交所和 ETF ticker（避免硬超时，选股流程已过滤）。

    北交所元数据拉取会硬超时（baostock/tonghuashun 对 bj. 会话挂起），且选股流程
    已过滤北交所；ETF 元数据拉取也会硬超时，且 ETF 仅参与纯技术面筛选，
    不依赖 PE/PB/行业等基本面元数据。

    Args:
        tickers: 原始 ticker 列表

    Returns:
        过滤后的 ticker 列表
    """
    tickers = [t for t in tickers if not t.startswith("bj.")]
    tickers = [t for t in tickers if not is_etf_ticker(t)]
    return tickers


def _build_results(
    tickers: list[str], name_map: dict[str, _StockMetadata]
) -> tuple[dict[str, _StockMetadata | None], int, int, float]:
    """构建元数据结果映射，全部使用 akshare 数据。

    对 akshare 中未找到的 ticker 保留空记录。

    Args:
        tickers: 待处理的 ticker 列表
        name_map: akshare 批量获取的元数据映射

    Returns:
        (results, 成功数, 失败数, 耗时秒)
    """
    results: dict[str, _StockMetadata | None] = {}
    success = 0
    failed = 0
    start_time = time.time()

    for ticker in tickers:
        if ticker in name_map:
            results[ticker] = name_map[ticker]
            success += 1
        else:
            # akshare 中未找到的 ticker，保留空记录
            results[ticker] = {
                "ticker": ticker,
                "name": "",
                "industry": None,
                "pe_ttm": None,
                "pb": None,
                "ipo_date": None,
                "out_date": None,
                "is_st": False,
                "source": "akshare",
            }
            failed += 1

    elapsed = time.time() - start_time
    return results, success, failed, elapsed


def _log_collection_result(success: int, failed: int, elapsed: float) -> None:
    """记录元数据拉取结果日志。

    Args:
        success: 成功数量
        failed: 失败数量
        elapsed: 耗时（秒）
    """
    logger.info(f"\n{'=' * 60}")
    logger.info("✅ 参考数据拉取完成!")
    logger.info(f"  成功: {success}, 失败: {failed}")
    logger.info(f"  耗时: {elapsed:.1f}s ({elapsed / 60:.1f}min)")


def _calc_dividend_yields_for_tickers(
    results: dict[str, _StockMetadata | None], tickers: list[str]
) -> None:
    """批量计算并填充缺失股息率的 A 股元数据。

    仅对 ``dividend_yield is None`` 的 ticker 调用 Fuyao API 补计算；
    未配置 API Key 时直接 warning 返回，不做任何网络请求。

    Args:
        results: {归一化 ticker: 元数据 dict 或 None}，原位更新 ``dividend_yield`` 字段。
        tickers: 需要尝试计算股息率的 ticker 列表（已过滤 bj./hk./ETF）。
    """
    dividend_yield_missing: list[str] = []
    for t in tickers:
        if t.startswith("hk."):
            continue
        meta_val: _StockMetadata | None = results.get(t)
        if (
            meta_val is not None
            and isinstance(meta_val, dict)
            and meta_val.get("dividend_yield") is None
        ):
            dividend_yield_missing.append(t)
    _calc_dividend_yields_batch(results, dividend_yield_missing)


def _persist_metadata(
    results: dict[str, _StockMetadata | None], use_checkpoint: bool = True
) -> None:
    """保存元数据到 JSON（原子写，避免读到半截文件）。

    全量模式写入前清理旧 metadata 中的 key（防止 key 格式不一致的残留数据
    被并入新结果）。

    Args:
        results: 待持久化的元数据映射
        use_checkpoint: 是否完成后清除 checkpoint，默认 True
    """
    # 清理旧 metadata 中不在当前结果的 key（防止残留数据）
    stale = [k for k in _load_existing_metadata() if k not in results]
    for k in stale:
        results.setdefault(k, None)

    # 写入前备份 metadata.json，防止误删除/覆盖
    from scripts.backup_cache import backup_all

    backup_all(keep=10)

    _atomic_write_json(METADATA_FILE, results)
    logger.info(f"  已保存至: {METADATA_FILE}")

    # 全量模式完成后清除 checkpoint
    if use_checkpoint:
        clear_checkpoint()
        logger.info("  🧹 checkpoint 已清除")


def _backfill_missing_names() -> None:
    """每日仅补漏：对 kline_cache 中名称缓存尚缺失的 ticker 做逐只名称补漏。

    已命中缓存的 ticker 不联网，符合「命中即本地使用、仅补缺失」的原则。
    """
    try:
        _all_tickers = get_all_tickers()
        _candidates = [
            t for t in _all_tickers if not t.startswith(("bj.", "hk."))
        ]
        _conn = get_sqlite_conn(CACHE_DB)
        try:
            _missing = get_missing_name_tickers(_conn, _candidates)
            if _missing:
                logger.info(
                    f"🔍 本地名称缓存缺失 {len(_missing)} 只，开始逐只补漏（每日仅补缺失）"
                )
                refresh_missing_stock_names(
                    _conn, _missing, _query_name_baostock
                )
            else:
                logger.debug("📛 股票名称缓存已完整，无需补漏")
        finally:
            _conn.close()
    except Exception as e:
        logger.debug(f"名称缓存补漏失败（不影响元数据主流程）: {e}")


def main(
    tickers_input: list[str] | None = None,
    resume: bool = False,
    use_checkpoint: bool = True,
) -> None:
    """启动参考数据（元数据）拉取主流程。

    流程：
      1. akshare 批量获取元数据（名称、PE/PB 等）
      2. 种入本地名称缓存
      3. 过滤北交所/ETF ticker
      4. 构建结果映射并记录统计日志
      5. Fuyao API 补计算股息率
      6. 持久化到 JSON（原子写 + 备份）
      7. 补漏缺失名称

    Args:
        tickers_input: 指定 ticker 列表，None 表示全量
        resume: 是否断点续传（从 checkpoint 恢复）
        use_checkpoint: 是否完成后清除 checkpoint
    """
    # 调用时加载 .env，避免模块 import 时的顶层副作用
    _load_env()

    logger.info("📊 启动参考数据（元数据）拉取")

    name_map = _collect_metadata()
    _seed_name_cache(name_map)

    tickers = tickers_input or get_all_tickers()
    tickers = _filter_tickers(tickers)

    logger.info(f"📋 共 {len(tickers)} 只股票待写入元数据（全量模式）")

    results, success, failed, elapsed = _build_results(tickers, name_map)
    _log_collection_result(success, failed, elapsed)

    # A 股股息率补计算（Fuyao API，仅对 dividend_yield 为 None 的票补算）
    _calc_dividend_yields_for_tickers(results, tickers)

    # 持久化到 JSON
    _persist_metadata(results, use_checkpoint=use_checkpoint)

    # 每日仅补漏：名称缓存缺失补漏
    _backfill_missing_names()

    logger.info(f"{'=' * 60}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="批量拉取股票元数据（参考数据）")
    parser.add_argument("--tickers", default=None, help="股票代码（逗号分隔），空=全量（部分拉取合并进现有文件）")
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="忽略 24h TTL，强制全量刷新",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="断点续传：从 checkpoint 恢复，仅重拉失败/未完成的 ticker",
    )
    parser.add_argument(
        "--reset-checkpoint",
        action="store_true",
        help="清除 checkpoint 并从头开始（与 --resume 互斥）",
    )
    args = parser.parse_args()

    if args.reset_checkpoint:
        clear_checkpoint()
        logger.info("🧹 checkpoint 已清除，将全量重拉")
        main(tickers_input=None, resume=False)
    elif args.force_refresh:
        main(tickers_input=None, resume=args.resume)
    else:
        tickers_list = (
            [t.strip() for t in args.tickers.split(",") if t.strip()] if args.tickers else None
        )
        if tickers_list:
            main(tickers_input=tickers_list)
        else:
            refresh_metadata_if_stale()
