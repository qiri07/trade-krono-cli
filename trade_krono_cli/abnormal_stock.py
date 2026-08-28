"""
abnormal_stock — 异常股票检测与标记模块。

职责：
  · 在流水线启动前批量预检股票状态（停牌 / ST / 退市 / 次新）
  · K 线数据完整性校验（缺失率超限告警）
  · 根据异常标记上调风险分
  · 结构化标记输出（AbnormalityFlag），供 StockMeta / StockFilter 消费

使用方式：
    # 预检一批股票
    flags_map = precheck_stock_status(["sh.600519", "sz.000001"], "2026-08-13")

    # 检查 K 线完整性
    ok, reason = check_kline_completeness(df, "sh.600519", min_completeness=0.85)

    # 风险分上调
    boosted = apply_abnormality_risk_boost(base_risk=40.0, flags=["ST", "SUSPENDED"])
    # → 90.0
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from loguru import logger

# ═══════════════════════════════════════════════════════
# 枚举与数据结构
# ═══════════════════════════════════════════════════════


class StockAbnormality(str, Enum):
    """异常类型枚举。"""

    SUSPENDED = "SUSPENDED"  # 停牌
    ST = "ST"  # ST/*ST 标的
    DELISTED = "DELISTED"  # 已退市
    NEW_STOCK = "NEW_STOCK"  # 次新股（上市不足阈值天数）
    DATA_INSUFFICIENT = "DATA_INSUFFICIENT"  # K 线数据不足


@dataclass(frozen=True)
class AbnormalityFlag:
    """
    单只股票的异常标记。

    Parameters
    ----------
    ticker : 股票代码
    flags : list[StockAbnormality]
        异常类型列表（可为空，表示正常）
    severity : float
        综合严重程度 0.0–1.0
    reason : str
        人工可读的原因说明
    """

    ticker: str
    flags: list[StockAbnormality] = field(default_factory=list)
    severity: float = 0.0
    reason: str = ""

    @property
    def is_normal(self) -> bool:
        return len(self.flags) == 0

    def flag_names(self) -> list[str]:
        return [f.value for f in self.flags]


# 风险分上调幅度（绝对分值）
_RISK_BOOST_MAP: dict[StockAbnormality, float] = {
    StockAbnormality.SUSPENDED: 30.0,
    StockAbnormality.ST: 20.0,
    StockAbnormality.DELISTED: 50.0,  # 退市股高风险
    StockAbnormality.NEW_STOCK: 10.0,
    StockAbnormality.DATA_INSUFFICIENT: 15.0,
}


# ═══════════════════════════════════════════════════════
# K 线完整性校验
# ═══════════════════════════════════════════════════════


def check_kline_completeness(
    df,
    ticker: str,
    min_completeness: float = 0.85,
) -> tuple[bool, str]:
    """
    校验 K 线数据完整性。

    计算方式：
      1. 计算应有效率日期范围内的实际交易日数
      2. 统计 NaN / 断点（相邻日期差 > 2 个交易日）的比例
      3. 完整率 = 有效行数 / 期望行数

    Parameters
    ----------
    df : pandas.DataFrame
        含 timestamps 列的 K 线数据
    ticker : str
        股票代码
    min_completeness : float
        最低完整率阈值（默认 0.85 = 85%）

    Returns
    -------
    (passed, reason)
      passed  — True 表示通过，False 表示未达标
      reason  — 详细说明
    """
    import pandas as pd

    if df is None or len(df) == 0:
        return False, f"{ticker}: K 线数据为空"

    if "timestamps" not in df.columns:
        return False, f"{ticker}: 缺少 timestamps 列"

    ts = pd.to_datetime(df["timestamps"])
    first_ts = ts.iloc[0]
    last_ts = ts.iloc[-1]

    # 计算应有效率交易日数
    expected_days = len(pd.bdate_range(start=first_ts, end=last_ts))
    if expected_days <= 0:
        return False, f"{ticker}: 日期范围为空"

    actual_days = len(df)

    # 计算断点（相邻交易日间隔 > 3 日历日，即跨越周末以上）
    gaps = []
    for i in range(1, len(ts)):
        delta = (ts.iloc[i] - ts.iloc[i - 1]).days
        if delta > 3:  # 周末间隔为 3 天，>3 表示缺失交易日
            gaps.append(delta)

    gap_days = sum(gaps)
    completeness = actual_days / expected_days

    reasons = []
    if completeness < min_completeness:
        reasons.append(
            f"完整率 {completeness:.1%} < {min_completeness:.0%}"
            f"（{actual_days}/{expected_days} 日）"
        )
    if gaps:
        reasons.append(f"发现 {len(gaps)} 个断点，共缺失 {gap_days} 个交易日")

    if not reasons:
        return True, f"{ticker}: 完整率 {completeness:.1%}，无断点"

    return False, f"{ticker}: " + "; ".join(reasons)


# ═══════════════════════════════════════════════════════
# 风险分上调
# ═══════════════════════════════════════════════════════


def apply_abnormality_risk_boost(
    base_risk_score: float,
    flags: list[str],
    enabled: bool = True,
    strategy: str = "fixed_boost",
    params: Optional[dict] = None,
) -> float:
    """
    根据异常标记上调风险分。

    通过 RiskBoostStrategy Registry 分发，支持可插拔策略。

    Parameters
    ----------
    base_risk_score : float
        原始风险分（0-100）
    flags : list[str]
        异常类型列表，如 ["ST", "SUSPENDED"]
    enabled : bool
        是否启用风险加分
    strategy : str
        策略名称：fixed_boost / scaled_boost / diminishing_boost
    params : dict | None
        策略参数（multiplier / diminishing_power）

    Returns
    -------
    float
        上调后的风险分（0-100）
    """
    if not enabled:
        return base_risk_score

    from trade_krono_cli.scoring import get_risk_boost_registry

    registry = get_risk_boost_registry()
    booster = registry.get(strategy) or registry.get("fixed_boost")
    result = booster.boost(base_risk_score, flags, params)
    boosted = result if isinstance(result, float) else result.boosted_risk
    total_boost = result.total_boost if hasattr(result, "total_boost") else 0.0

    if total_boost > 0:
        logger.info(
            f"📈 {boosted:.0f} 风险分上调: "
            f"{base_risk_score:.0f} + {total_boost:.0f} "
            f"(strategy={strategy}, flags={flags})"
        )
    return boosted


# ═══════════════════════════════════════════════════════
# 股票状态预检
# ═══════════════════════════════════════════════════════

# baostock ST 股票名称模式（复用 trading_constraints 的约定）
_ST_PATTERNS = re.compile(r"^(ST|\*ST|SST|N ST)", re.IGNORECASE)

# 模块级缓存：{ticker: is_st_bool}
_st_cache: dict[str, bool] = {}


def _check_st_status_cached(ticker: str) -> bool:
    """
    检查是否为 ST 标的（带模块级缓存）。
    使用 BaostockProvider 统一访问数据源。
    """
    if ticker in _st_cache:
        return _st_cache[ticker]

    try:
        from trade_krono_cli.data_providers.baostock_provider import BaostockProvider

        provider = BaostockProvider()
        result = provider.check_st_status(ticker)
    except (ImportError, RuntimeError) as e:
        logger.debug(f"ST 检测初始化失败 {ticker}: {e}")
        result = False
    except Exception as e:
        logger.debug(f"ST 检测异常 {ticker}: {str(e)[:200]}")
        result = False

    _st_cache[ticker] = result
    return result


def _check_suspended_via_kline(
    ticker: str,
    eval_date: str,
    max_gap_trading_days: int = 10,
) -> tuple[bool, str]:
    """
    通过 K 线新鲜度检测股票是否停牌。

    Parameters
    ----------
    ticker : 股票代码
    eval_date : 评估日期
    max_gap_trading_days : 最大允许的交易日间隔

    Returns
    -------
    (is_suspended, reason)
    """
    from trade_krono_cli.data import fetch_lookback

    try:
        df = fetch_lookback(ticker, eval_date, lookback=5, use_cache=False)
    except RuntimeError as e:
        msg = str(e)
        if "数据过旧" in msg or "停牌" in msg:
            return True, msg
        # 其他错误（如数据不足）不归入停牌
        return False, msg

    # 额外检查：如果 K 线获取成功但行数极少（<5行），也视为异常
    if len(df) < 5:
        return True, f"{ticker}: K 线仅 {len(df)} 行，疑似长期停牌"

    return False, ""


def _check_delisted(ticker: str) -> bool:
    """
    检查股票是否已退市。
    使用 BaostockProvider 统一访问数据源。
    """
    try:
        from trade_krono_cli.data_providers.baostock_provider import BaostockProvider

        provider = BaostockProvider()
        return provider.check_delisted(ticker)
    except (ImportError, RuntimeError):
        logger.debug("baostock 未安装，退市检测跳过")
        return False
    except Exception as e:
        logger.debug(f"退市检测异常 {ticker}: {str(e)[:200]}")
        return False


def _check_new_stock(
    ticker: str,
    eval_date: str,
    min_listing_days: int = 60,
) -> tuple[bool, str]:
    """
    检查是否为次新股（上市不足 min_listing_days 个交易日）。
    使用 BaostockProvider 统一访问数据源。
    """
    try:
        from trade_krono_cli.data_providers.baostock_provider import BaostockProvider

        provider = BaostockProvider()
        return provider.check_new_stock(ticker, eval_date, min_listing_days)
    except (ImportError, RuntimeError):
        logger.debug("baostock 未安装，次新股检测跳过")
    except Exception as e:
        logger.debug(f"次新股检测异常 {ticker}: {str(e)[:200]}")

    return False, ""


def precheck_stock_status(
    tickers: list[str],
    eval_date: str,
    min_listing_days: int = 60,
    max_gap_trading_days: int = 10,
    skip_suspended: bool = True,
    skip_new_stock: bool = True,
) -> dict[str, AbnormalityFlag]:
    """
    批量预检股票状态，返回每只股票的异常标记。

    Parameters
    ----------
    tickers : list[str]
        股票代码列表
    eval_date : str
        评估日期 YYYY-MM-DD
    min_listing_days : int
        次新股判定阈值（上市不足此天数视为次新）
    max_gap_trading_days : int
        停牌判定阈值（最后交易日距评估日超过此天数视为停牌）
    skip_suspended : bool
        是否标记停牌股（True=标记，用于后续过滤）
    skip_new_stock : bool
        是否标记次新股

    Returns
    -------
    dict[ticker, AbnormalityFlag]
    """
    from trade_krono_cli.security import validate_ticker

    results: dict[str, AbnormalityFlag] = {}
    flagged_count = 0

    for ticker in tickers:
        ticker = validate_ticker(ticker)
        flags: list[StockAbnormality] = []
        reasons: list[str] = []

        # 1. ST 检测
        if _check_st_status_cached(ticker):
            flags.append(StockAbnormality.ST)
            reasons.append("ST/*ST 标的")

        # 2. 退市检测
        if _check_delisted(ticker):
            flags.append(StockAbnormality.DELISTED)
            reasons.append("已退市")

        # 3. 停牌检测
        if skip_suspended:
            is_sus, sus_reason = _check_suspended_via_kline(ticker, eval_date, max_gap_trading_days)
            if is_sus:
                flags.append(StockAbnormality.SUSPENDED)
                reasons.append(sus_reason)

        # 4. 次新股检测
        if skip_new_stock:
            is_new, new_reason = _check_new_stock(ticker, eval_date, min_listing_days)
            if is_new:
                flags.append(StockAbnormality.NEW_STOCK)
                reasons.append(new_reason)

        # 构建 flag
        if flags:
            severity = _compute_severity(flags)
            flag = AbnormalityFlag(
                ticker=ticker,
                flags=flags,
                severity=severity,
                reason="; ".join(reasons),
            )
            flagged_count += 1
        else:
            flag = AbnormalityFlag(ticker=ticker, flags=[], severity=0.0, reason="")

        results[ticker] = flag

    logger.info(f"🔍 异常预检完成: {len(tickers)} 只股票中 {flagged_count} 只有异常标记")
    return results


def _compute_severity(flags: list[StockAbnormality]) -> float:
    """
    根据异常标记列表计算综合严重程度 0.0–1.0。

    多个异常取最大值（不累加，避免重复计分）。
    """
    if not flags:
        return 0.0
    # 各异常类型的严重度权重
    severity_map = {
        StockAbnormality.DELISTED: 1.0,
        StockAbnormality.SUSPENDED: 0.9,
        StockAbnormality.ST: 0.7,
        StockAbnormality.DATA_INSUFFICIENT: 0.5,
        StockAbnormality.NEW_STOCK: 0.3,
    }
    return max(severity_map.get(f, 0.0) for f in flags)
