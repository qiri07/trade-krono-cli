"""数据层 — A 股 K 线获取 + Kronos 格式转换。

支持多数据源（baostock / akshare / mootdx / tushare），
通过 DataProviderFactory 自动降级。
保留原有 baostock 直调接口作为兼容层。
"""

from __future__ import annotations

import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

import pandas as pd
from loguru import logger

# ── baostock 会话管理（已提取到 bs_session.py）──────────────────────────────
from trade_krono_cli.bs_session import (  # noqa: F401
    _bs,
    _ensure_bs_import,
    _ensure_bs_login,
    _get_limiter,
    clear_baostock_globals,
)
from trade_krono_cli.cache import get_cache
from trade_krono_cli.data_providers import DataProviderFactory, get_data_factory
from trade_krono_cli.security import retry, validate_date, validate_ticker
from trade_krono_cli.utils.helpers import (
    next_business_days,  # noqa: F401
    validate_data_freshness,  # noqa: F401
)
from trade_krono_cli.utils.helpers import safe_float as _safe_float  # noqa: F401

# ═══════════════════════════════════════════════════════
# 核心：拉取 K 线
# ═══════════════════════════════════════════════════════


@retry(max_attempts=3, base_delay=2.0, exceptions=(RuntimeError, ConnectionError))
def fetch_kline(
    ticker: str,
    start_date: str,
    end_date: str,
    frequency: str = "d",
    adjustflag: str = "1",
    use_cache: bool = True,
) -> pd.DataFrame:
    """拉取 A 股 K 线并转为 Kronos 标准格式。

    优先使用 DataProviderFactory（多数据源自动降级），
    失败时回退到原有 baostock 直调逻辑。

    Returns
    -------
    DataFrame with columns: open, high, low, close, volume, amount, timestamps

    """
    ticker = validate_ticker(ticker)
    start_date = validate_date(start_date)
    end_date = validate_date(end_date)

    from trade_krono_cli.cache import get_cache

    cache = get_cache()
    if use_cache:
        cached = cache.get_kline(ticker, start_date, end_date, frequency, adjustflag)
        if cached is not None:
            logger.debug(f"📦 K线缓存命中: {ticker} {start_date}~{end_date}")
            return cached

    # ── 路径 1：通过工厂（多数据源 + 自动降级）──────────────────────────
    try:
        factory = get_data_factory()
        kline_data = factory.fetch_kline(ticker, start_date, end_date, frequency, adjustflag)
        if kline_data is not None and not kline_data.is_empty:
            out = kline_data.to_dataframe()
            # 写缓存（永久）
            from trade_krono_cli.cache import _KLINE_HISTORICAL_TTL

            cache.set_kline(
                ticker,
                start_date,
                end_date,
                frequency,
                out,
                ttl=_KLINE_HISTORICAL_TTL,
                adjustflag=adjustflag,
            )
            logger.debug(
                f"✅ K 线就绪（via {kline_data.source if hasattr(kline_data, 'source') else 'factory'}）: {ticker} 共 {len(out)} 行",
            )
            return out
    except Exception as e:
        logger.warning(f"Factory K 线拉取失败 {ticker}: {str(e)[:150]}，回退 baostock 直调")

    # ── 路径 2：直接调用 baostock（兼容层）──────────────────────────────
    _ensure_bs_import()
    _get_limiter().acquire()
    _ensure_bs_login()

    bs_code = ticker

    logger.debug(
        f"📥 拉取 K 线（baostock 直调）: {bs_code} {start_date}~{end_date} freq={frequency}",
    )

    rs = _bs.query_history_k_data_plus(  # type: ignore
        bs_code,
        "date,open,high,low,close,volume,amount",
        start_date=start_date,
        end_date=end_date,
        frequency=frequency,
        adjustflag=adjustflag,
    )

    if rs.error_code != "0":
        msg = f"baostock 查询失败 [{bs_code}]: {rs.error_msg}"
        raise RuntimeError(msg)

    rows = []
    while rs.next():
        rows.append(rs.get_row_data())

    if not rows:
        msg = f"空数据: {bs_code} {start_date}~{end_date}"
        raise RuntimeError(msg)

    df = pd.DataFrame(rows, columns=rs.fields)
    df = df.dropna(subset=["close"])

    # 使用 rs.fields 动态映射列名，避免硬编码索引依赖
    _col_map = {c: i for i, c in enumerate(rs.fields)}
    _date_col = rs.fields[_col_map.get("date", 0)]
    _open_col = rs.fields[_col_map.get("open", 1)]
    _high_col = rs.fields[_col_map.get("high", 2)]
    _low_col = rs.fields[_col_map.get("low", 3)]
    _close_col = rs.fields[_col_map.get("close", 4)]
    _volume_col = rs.fields[_col_map.get("volume", 5)]
    _amount_col = rs.fields[_col_map.get("amount", 6)]

    out = pd.DataFrame(
        {
            "timestamps": pd.to_datetime(df[_date_col]),
            "open": df[_open_col].astype(float),
            "high": df[_high_col].astype(float),
            "low": df[_low_col].astype(float),
            "close": df[_close_col].astype(float),
            "volume": df[_volume_col].astype(float),
            "amount": df[_amount_col].astype(float),
        },
    ).reset_index(drop=True)

    # 写缓存：永久缓存
    from trade_krono_cli.cache import _KLINE_HISTORICAL_TTL

    cache.set_kline(
        ticker,
        start_date,
        end_date,
        frequency,
        out,
        ttl=_KLINE_HISTORICAL_TTL,
        adjustflag=adjustflag,
    )
    logger.debug(f"✅ K 线就绪: {ticker} 共 {len(out)} 行")

    return out


def fetch_lookback(
    ticker: str,
    end_date: str,
    lookback: int = 400,
    frequency: str = "d",
    buffer_days: int = 60,
    use_cache: bool = True,
    adjustflag: str = "1",
) -> pd.DataFrame:
    """自动计算 start_date，拉取足够历史数据。

    若缓存中已有部分数据，仅增量拉取缺失区间（避免重复下载）。
    """
    ticker = validate_ticker(ticker)
    end_date = validate_date(end_date)
    end = datetime.strptime(end_date, "%Y-%m-%d")

    if frequency == "d":
        start = end - timedelta(days=lookback * 2 + buffer_days)
    else:
        start = end - timedelta(days=lookback // 48 + 10)
    start_s = start.strftime("%Y-%m-%d")

    df = fetch_kline_incremental(
        ticker,
        start_s,
        end_date,
        frequency=frequency,
        adjustflag=adjustflag,
        use_cache=use_cache,
    )
    if len(df) < lookback:
        msg = f"数据不足: {ticker} 仅 {len(df)} 行 < lookback {lookback}（检查停牌/新上市）"
        raise RuntimeError(
            msg,
        )
    # 校验数据末尾与评估日期的间隔，防止停牌期间数据过时
    validate_data_freshness(df, end_date, ticker)
    return df


def fetch_kline_incremental(
    ticker: str,
    start_date: str,
    end_date: str,
    frequency: str = "d",
    adjustflag: str = "1",
    use_cache: bool = True,
) -> pd.DataFrame:
    """增量拉取 K 线数据。

    策略：
      1. 先查本地缓存，判断已有日期覆盖范围
      2. 若缓存已完整覆盖 [start_date, end_date]，直接返回缓存数据
      3. 若缓存有部分数据（覆盖到某个中间日期），仅拉取缺失的尾部区间
      4. 将新拉取的数据与缓存合并，更新缓存条目

    Returns
    -------
    合并后的完整 DataFrame

    """
    ticker = validate_ticker(ticker)
    start_date = validate_date(start_date)
    end_date = validate_date(end_date)

    cache = get_cache()

    if use_cache:
        cached_range = cache.get_cached_date_range(ticker, freq=frequency, adjustflag=adjustflag)
    else:
        cached_range = None

    if cached_range is not None:
        cached_start, cached_end = cached_range
        logger.debug(f"📦 {ticker} 缓存覆盖: {cached_start} ~ {cached_end}")

        # ── 情况 1：缓存已完整覆盖请求范围，无需重新拉取 ──────────────
        if cached_start <= start_date and cached_end >= end_date:
            df = fetch_kline(
                ticker,
                start_date,
                end_date,
                frequency=frequency,
                adjustflag=adjustflag,
                use_cache=True,
            )
            logger.info(f"✅ {ticker} 增量拉取: 缓存已完整覆盖，跳过网络请求 ({len(df)} 行)")
            return df

        # ── 情况 2：缓存有重叠，需补拉缺失区间 ────────────────────────
        # 需要补拉：从 max(start_date, 缓存end+1天) 到 end_date
        # 简化：直接从 cached_end 之后开始拉取（含当天以覆盖边界）
        fetch_start = cached_end
        # 若缓存完全在请求范围之前，直接拉取整个 range
        if cached_end < start_date:
            fetch_start = start_date
            logger.info(
                f"🔄 {ticker} 增量拉取: 缓存过期/过期前，重新拉取 {fetch_start} ~ {end_date}",
            )
        elif cached_end >= end_date:
            # 缓存已超出或刚好到达请求范围，无需补拉（但需通过 fetch_kline 限制返回区间）
            fetch_start = start_date
            logger.info(
                f"🔄 {ticker} 增量拉取: 缓存已覆盖至 {cached_end}，仅返回 {fetch_start} ~ {end_date}",
            )
        else:
            # 缓存与请求范围有重叠，从 cached_end 下一天开始补拉
            next_day = (datetime.strptime(cached_end, "%Y-%m-%d") + timedelta(days=1)).strftime(
                "%Y-%m-%d",
            )
            fetch_start = next_day
            logger.info(
                f"🔄 {ticker} 增量拉取: 补拉 {fetch_start} ~ {end_date} "
                f"（已有 {cached_start} ~ {cached_end}）",
            )

        # 拉取缺失段
        new_df = fetch_kline(
            ticker,
            fetch_start,
            end_date,
            frequency=frequency,
            adjustflag=adjustflag,
            use_cache=True,
        )

        # 读取旧缓存数据
        old_df = fetch_kline(
            ticker,
            cached_start,
            cached_end,
            frequency=frequency,
            adjustflag=adjustflag,
            use_cache=True,
        )

        # 合并：优先保留新数据（去重）
        merged = _merge_kline_dfs(old_df, new_df)
        logger.info(
            f"✅ {ticker} 增量拉取合并完成: 旧 {len(old_df)} 行 "
            f"+ 新 {len(new_df)} 行 → 合并后 {len(merged)} 行",
        )

        # 写回缓存（永久）
        _write_merged_cache(cache, ticker, merged, start_date, end_date, frequency, adjustflag)
        return merged

    # ── 情况 3：无缓存，全量拉取 ────────────────────────────────────
    logger.info(f"📥 {ticker} 无缓存，全量拉取 {start_date} ~ {end_date}")
    return fetch_kline(
        ticker,
        start_date,
        end_date,
        frequency=frequency,
        adjustflag=adjustflag,
        use_cache=True,
    )


def _write_merged_cache(
    cache,
    ticker: str,
    df: pd.DataFrame,
    start_date: str,
    end_date: str,
    frequency: str,
    adjustflag: str = "1",
) -> None:
    """将合并后的 K 线数据写回缓存，全部以永久缓存写入。"""
    from trade_krono_cli.cache import _KLINE_HISTORICAL_TTL

    seg_start = df["timestamps"].iloc[0].strftime("%Y-%m-%d")
    seg_end = df["timestamps"].iloc[-1].strftime("%Y-%m-%d")
    cache.set_kline(
        ticker,
        seg_start,
        seg_end,
        frequency,
        df,
        ttl=_KLINE_HISTORICAL_TTL,
        adjustflag=adjustflag,
    )
    logger.debug(f"📦 永久缓存: {ticker} {seg_start}~{seg_end}")


def _merge_kline_dfs(old_df: pd.DataFrame, new_df: pd.DataFrame) -> pd.DataFrame:
    """合并新旧 K 线 DataFrame，去重并按时间排序。

    去重策略：
      - 新数据始终优先（覆盖重叠行）
      - 旧数据中未被新数据覆盖的前段保留
      - 新数据中超出旧数据范围的后段追加
    """
    if old_df is None or len(old_df) == 0:
        return new_df.copy() if new_df is not None else pd.DataFrame()
    if new_df is None or len(new_df) == 0:
        return old_df.copy()

    # 统一 timestamps 类型（来源可能返回 str 或 Timestamp）
    old_df = old_df.copy()
    new_df = new_df.copy()
    old_df["timestamps"] = pd.to_datetime(old_df["timestamps"])
    new_df["timestamps"] = pd.to_datetime(new_df["timestamps"])

    old_ts = old_df["timestamps"]
    new_ts = new_df["timestamps"]

    max_new_ts = new_ts.max()

    # 保留旧数据中严格早于新数据最大时间的部分
    # （等值行由 drop_duplicates keep="last" 保证新数据优先）
    keep_old = old_ts < max_new_ts
    keep_old_df = old_df[keep_old] if keep_old.any() else pd.DataFrame()

    # 组合：旧前段 + 全部新数据
    parts = [keep_old_df, new_df]
    merged = pd.concat([p for p in parts if len(p) > 0], ignore_index=True)

    # 按 timestamps 排序并去重（保留最后一条，即新数据优先）
    merged = merged.sort_values("timestamps").reset_index(drop=True)
    return merged.drop_duplicates(subset=["timestamps"], keep="last").reset_index(drop=True)


def fetch_kline_parallel(
    ticker: str,
    start_date: str,
    end_date: str,
    frequency: str = "d",
    adjustflag: str = "1",
    use_cache: bool = True,
    workers: int = 3,
) -> tuple[pd.DataFrame | None, str]:
    """并发尝试多个 Provider 拉取 K 线，任一成功即返回。

    用于批量同步场景，避免单 Provider 慢导致的整体阻塞。

    Parameters
    ----------
    ticker : str
        股票代码（带交易所前缀，如 sh.600519）
    start_date : str
        起始日期 YYYY-MM-DD
    end_date : str
        结束日期 YYYY-MM-DD
    frequency : str
        频率，默认 "d"（日 K）
    adjustflag : str
        复权方式，默认 "1"（前复权）
    use_cache : bool
        是否使用缓存，默认 True
    workers : int
        并发尝试的 Provider 数量，默认 3

    Returns
    -------
    tuple[pd.DataFrame | None, str]
        (数据, provider名称)；全部失败时返回 (None, "")
    """
    ticker = validate_ticker(ticker)
    start_date = validate_date(start_date)
    end_date = validate_date(end_date)

    factory = get_data_factory()
    chain = factory._provider_chain_for_ticker(ticker)
    # 过滤出有 K 线能力的 Provider
    candidate_names = [
        name
        for name in chain
        if factory.get_provider(name) is not None
        and getattr(factory.get_provider(name), "supports_kline", False)
    ]
    if not candidate_names:
        logger.warning(f"❌ {ticker} 无可用的 K 线 Provider")
        return None, ""

    # 取前 workers 个 Provider
    names_to_try = candidate_names[:workers]
    logger.debug(f"🔀 {ticker} 并发尝试 Provider: {names_to_try}")

    results: list[tuple[str, pd.DataFrame | None, Exception | None]] = []

    with ThreadPoolExecutor(max_workers=len(names_to_try)) as pool:
        future_to_name = {
            pool.submit(
                _try_provider_single,
                factory,
                name,
                ticker,
                start_date,
                end_date,
                frequency,
                adjustflag,
                use_cache,
            ): name
            for name in names_to_try
        }
        for future in as_completed(future_to_name):
            name = future_to_name[future]
            try:
                df, err = future.result()
                results.append((name, df, err))
            except Exception as e:
                results.append((name, None, e))

    # 找出首个成功的结果
    for name, df, err in results:
        if df is not None and len(df) > 0:
            logger.info(f"✅ K 线获取成功（并发）: {ticker} ← {name} ({len(df)} 条)")
            return df, name

    # 汇总失败原因
    fail_reasons = [
        f"{name}({type(e).__name__}: {str(e)[:60]})"
        for name, df, err in results
        for e in [err]
        if e is not None
    ] or [f"{name}: no data" for name, df, _ in results if df is None]
    logger.warning(f"❌ {ticker} 所有并发 Provider 均失败: {', '.join(fail_reasons[:2])}")
    return None, ""


def _try_provider_single(
    factory: DataProviderFactory,
    name: str,
    ticker: str,
    start_date: str,
    end_date: str,
    frequency: str,
    adjustflag: str,
    use_cache: bool,
) -> tuple[pd.DataFrame | None, Exception | None]:
    """单个 Provider 的 K 线拉取（供并发调用）。"""
    try:
        provider = factory.get_provider(name)
        if provider is None:
            return None, RuntimeError(f"{name}: provider 未初始化")
        data = provider.fetch_kline(ticker, start_date, end_date, frequency, adjustflag)
        if data is not None and not data.is_empty:
            return data.to_dataframe(), None
        return None, None  # 无数据，非异常
    except Exception as e:
        return None, e


# 腾讯财经接口字段索引（qt.gtimg.cn 协议）
# https://stock.finance.sina.com.cn/
# 索引对应字段如下（部分字段可能因股票类型不同而缺失）：
_TQ_PRICE = 3  # 当前价（元）
_TQ_PE = 39  # 市盈率（动态）
_TQ_PB = 46  # 市净率
_TQ_MKT_CAP = 44  # 总市值（亿元）
_TQ_TURNOVER = 38  # 换手率（%）
# 最小字段数：需至少包含 _TQ_PRICE（索引 3）+ 分隔符，取保守阈值 45
_TQ_MIN_FIELDS = 45


def fetch_realtime_quote(ticker: str) -> dict:
    """腾讯财经实时估值（免费、无需 key）。
    返回 {price, pe, pb, market_cap, turnover} 或 {}。

    字段均安全解析：字段缺失或非数字时该项为 None，不抛异常。
    """
    ticker = validate_ticker(ticker)
    code = ticker.split(".")[-1]
    prefix = ticker.split(".")[0]

    url = f"https://qt.gtimg.cn/q={prefix}{code}"
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            raw = resp.read().decode("gbk", errors="ignore")
    except (urllib.error.URLError, OSError):
        return {}

    fields = raw.split("~")
    if len(fields) < _TQ_MIN_FIELDS:
        return {}

    return {
        "price": _safe_float(fields[_TQ_PRICE]),
        "pe": _safe_float(fields[_TQ_PE]) if len(fields) > _TQ_PE else None,
        "pb": _safe_float(fields[_TQ_PB]) if len(fields) > _TQ_PB else None,
        "market_cap": _safe_float(fields[_TQ_MKT_CAP]) if len(fields) > _TQ_MKT_CAP else None,
        "turnover": _safe_float(fields[_TQ_TURNOVER]) if len(fields) > _TQ_TURNOVER else None,
    }
