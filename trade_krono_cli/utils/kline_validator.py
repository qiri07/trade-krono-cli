"""K 线数据校验工具 — 时间升序、去重、缺失K线检测、脏数据告警。

职责：
  - validate_kline_order()     : 检查 timestamps 是否严格升序
  - validate_kline_dedup()     : 检查 timestamps 是否去重
  - detect_missing_dates()     : 检测交易日缺失（节假日除外）
  - detect_dirty_data()        : 检测价格/成交量异常值
  - KlineValidationResult      : 校验结果数据类
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import pandas as pd


@dataclass
class KlineValidationResult:
    """K 线数据校验结果。"""

    is_valid: bool = True
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    info: list[str] = field(default_factory=list)

    def add_error(self, msg: str) -> None:
        self.errors.append(msg)
        self.is_valid = False

    def add_warning(self, msg: str) -> None:
        self.warnings.append(msg)

    def add_info(self, msg: str) -> None:
        self.info.append(msg)

    def summary(self) -> str:
        lines = [f"K线校验结果: {'✅ 通过' if self.is_valid else '❌ 失败'}"]
        if self.errors:
            lines.append(f"  错误 {len(self.errors)} 条:")
            for e in self.errors:
                lines.append(f"    ❌ {e}")
        if self.warnings:
            lines.append(f"  警告 {len(self.warnings)} 条:")
            for w in self.warnings:
                lines.append(f"    ⚠️  {w}")
        if self.info:
            lines.append(f"  信息 {len(self.info)} 条:")
            for i in self.info:
                lines.append(f"    ℹ️  {i}")
        return "\n".join(lines)


def validate_kline_order(df: pd.DataFrame, ticker: str = "") -> KlineValidationResult:
    """校验 K 线 timestamps 是否严格升序。

    Parameters
    ----------
    df : pd.DataFrame
        包含 "timestamps" 列的 DataFrame
    ticker : str
        股票代码，用于日志输出

    Returns
    -------
    KlineValidationResult
    """
    result = KlineValidationResult()

    if "timestamps" not in df.columns:
        result.add_error(f"{ticker}: 缺少 timestamps 列")
        return result

    ts = pd.to_datetime(df["timestamps"])
    if ts.empty:
        result.add_warning(f"{ticker}: 空数据")
        return result

    # 检查是否升序
    is_sorted = ts.is_monotonic_increasing
    if not is_sorted:
        # 找出第一个非升序的位置
        diffs = ts.diff()
        invalid_idx = diffs[diffs <= timedelta(0)].index
        if len(invalid_idx) > 0:
            first_bad = invalid_idx[0]
            result.add_error(
                f"{ticker}: 时间戳非升序，位置 {first_bad} 处 "
                f"({ts.iloc[first_bad - 1] if first_bad > 0 else 'start'} >= {ts.iloc[first_bad]})"
            )

    # 检查是否有重复日期
    duplicates = ts[ts.duplicated(keep=False)]
    if len(duplicates) > 0:
        result.add_warning(f"{ticker}: 发现 {len(duplicates)} 条重复时间戳")

    result.add_info(f"{ticker}: 共 {len(ts)} 条，时间范围 {ts.min()} ~ {ts.max()}")
    return result


def validate_kline_dedup(df: pd.DataFrame, ticker: str = "") -> KlineValidationResult:
    """校验 K 线数据是否去重。

    Parameters
    ----------
    df : pd.DataFrame
    ticker : str

    Returns
    -------
    KlineValidationResult
    """
    result = KlineValidationResult()

    if "timestamps" not in df.columns:
        result.add_error(f"{ticker}: 缺少 timestamps 列，无法去重校验")
        return result

    ts = pd.to_datetime(df["timestamps"])
    dup_count = ts.duplicated().sum()

    if dup_count > 0:
        result.add_warning(f"{ticker}: 发现 {dup_count} 条重复记录（按时间戳去重）")
    else:
        result.add_info(f"{ticker}: 无重复记录")

    return result


def detect_missing_dates(
    df: pd.DataFrame,
    ticker: str = "",
    exclude_weekends: bool = True,
) -> KlineValidationResult:
    """检测交易日缺失（节假日除外）。

    策略：
      1. 生成 [start_date, end_date] 之间的日历日序列
      2. 排除周末（可选）
      3. 对比实际存在的日期，找出缺失项
      4. 缺失超过 N 天则告警

    Parameters
    ----------
    df : pd.DataFrame
    ticker : str
    exclude_weekends : bool
        是否排除周末（默认 True，因为 A 股周末休市）

    Returns
    -------
    KlineValidationResult
    """
    result = KlineValidationResult()

    if "timestamps" not in df.columns:
        result.add_error(f"{ticker}: 缺少 timestamps 列")
        return result

    ts = pd.to_datetime(df["timestamps"])
    if ts.empty:
        result.add_warning(f"{ticker}: 空数据，跳过缺失检测")
        return result

    start_date = ts.min().date()
    end_date = ts.max().date()
    total_days = (end_date - start_date).days + 1

    # 生成预期交易日序列（排除周末）
    if exclude_weekends:
        expected_dates = set()
        current = start_date
        while current <= end_date:
            if current.weekday() < 5:  # 周一到周五
                expected_dates.add(current)
            current += timedelta(days=1)
    else:
        expected_dates = {
            start_date + timedelta(days=i)
            for i in range(total_days)
        }

    # 实际存在的日期
    actual_dates = set(ts.dt.date.unique())

    # 找出缺失日期
    missing_dates = sorted(expected_dates - actual_dates)

    if missing_dates:
        # 计算连续缺失天数，找出最长缺口
        max_gap = 0
        current_gap = 0
        gap_starts: list[date] = []
        current_start: date | None = None

        for d in missing_dates:
            if current_start is None:
                current_start = d
                current_gap = 1
            elif (d - list(missing_dates)[missing_dates.index(d) - 1]).days == 1:
                current_gap += 1
            else:
                if current_gap > max_gap:
                    max_gap = current_gap
                    gap_starts = [current_start]  # type: ignore[arg-type]
                elif current_gap == max_gap:
                    gap_starts.append(current_start)  # type: ignore[arg-type]
                current_start = d
                current_gap = 1

        if current_gap > max_gap:
            max_gap = current_gap
            if current_start is not None:
                gap_starts = [current_start]

        result.add_warning(
            f"{ticker}: 缺失 {len(missing_dates)}/{len(expected_dates)} 个交易日 "
            f"({len(expected_dates) - len(actual_dates)}/{total_days} 天)",
        )
        if max_gap >= 5:
            result.add_warning(
                f"{ticker}: ⚠️  检测到长缺口（≥{max_gap}天），可能数据异常",
            )
    else:
        result.add_info(f"{ticker}: 无缺失交易日")

    # 覆盖率
    coverage = len(actual_dates) / len(expected_dates) * 100 if expected_dates else 0
    result.add_info(f"{ticker}: 数据覆盖率 {coverage:.1f}%")

    return result


def detect_dirty_data(
    df: pd.DataFrame,
    ticker: str = "",
    price_zscore_threshold: float = 5.0,
    volume_zscore_threshold: float = 10.0,
) -> KlineValidationResult:
    """检测脏数据：价格异常值、成交量异常值。

    Parameters
    ----------
    df : pd.DataFrame
    ticker : str
    price_zscore_threshold : float
        价格 Z-score 阈值，超过此值视为异常（默认 5.0）
    volume_zscore_threshold : float
        成交量 Z-score 阈值（默认 10.0）

    Returns
    -------
    KlineValidationResult
    """
    result = KlineValidationResult()

    # 价格异常检测
    for col in ["open", "high", "low", "close"]:
        if col not in df.columns:
            continue
        series = pd.to_numeric(df[col], errors="coerce")
        if series.empty or series.std() == 0:
            continue
        zscores = ((series - series.mean()) / series.std()).abs()
        outliers = zscores[zscores > price_zscore_threshold]
        if len(outliers) > 0:
            result.add_warning(
                f"{ticker}: {col} 价格异常值 {len(outliers)} 条 "
                f"(Z-score > {price_zscore_threshold})"
            )

    # 成交量异常检测
    if "volume" in df.columns:
        vol = pd.to_numeric(df["volume"], errors="coerce")
        if not vol.empty and vol.std() > 0:
            zscores = ((vol - vol.mean()) / vol.std()).abs()
            outliers = zscores[zscores > volume_zscore_threshold]
            if len(outliers) > 0:
                result.add_warning(
                    f"{ticker}: volume 异常值 {len(outliers)} 条 "
                    f"(Z-score > {volume_zscore_threshold})"
                )

    # 价格逻辑校验：high >= low, high >= open, high >= close, low <= open, low <= close
    if all(c in df.columns for c in ["high", "low", "open", "close"]):
        logic_errors = 0
        if (df["high"] < df["low"]).any():
            logic_errors += (df["high"] < df["low"]).sum()
        if (df["high"] < df["open"]).any():
            logic_errors += (df["high"] < df["open"]).sum()
        if (df["high"] < df["close"]).any():
            logic_errors += (df["high"] < df["close"]).sum()
        if (df["low"] > df["open"]).any():
            logic_errors += (df["low"] > df["open"]).sum()
        if (df["low"] > df["close"]).any():
            logic_errors += (df["low"] > df["close"]).sum()

        if logic_errors > 0:
            result.add_error(f"{ticker}: 价格逻辑错误 {logic_errors} 条（high<low 等）")

    # 零值检测
    for col in ["open", "high", "low", "close"]:
        if col in df.columns:
            zero_count = (pd.to_numeric(df[col], errors="coerce") == 0).sum()
            if zero_count > 0:
                result.add_warning(f"{ticker}: {col} 有 {zero_count} 条零值")

    if result.is_valid:
        result.add_info(f"{ticker}: 脏数据检测通过")

    return result


def validate_kline_complete(
    df: pd.DataFrame,
    ticker: str = "",
    min_rows: int = 100,
    min_coverage: float = 0.85,
) -> KlineValidationResult:
    """综合校验 K 线数据完整性。

    Parameters
    ----------
    df : pd.DataFrame
    ticker : str
    min_rows : int
        最小行数阈值
    min_coverage : float
        最小覆盖率（0-1）

    Returns
    -------
    KlineValidationResult
    """
    result = KlineValidationResult()

    # 基础行数检查
    if len(df) < min_rows:
        result.add_warning(f"{ticker}: 数据量不足（{len(df)} 行 < {min_rows} 行阈值）")
    else:
        result.add_info(f"{ticker}: 数据量 {len(df)} 行，满足阈值")

    # 时间升序
    order_result = validate_kline_order(df, ticker)
    result.errors.extend(order_result.errors)
    result.warnings.extend(order_result.warnings)
    result.info.extend(order_result.info)

    # 去重
    dedup_result = validate_kline_dedup(df, ticker)
    result.warnings.extend(dedup_result.warnings)
    result.info.extend(dedup_result.info)

    # 缺失检测
    missing_result = detect_missing_dates(df, ticker)
    result.warnings.extend(missing_result.warnings)
    result.info.extend(missing_result.info)

    # 脏数据
    dirty_result = detect_dirty_data(df, ticker)
    result.errors.extend(dirty_result.errors)
    result.warnings.extend(dirty_result.warnings)
    result.info.extend(dirty_result.info)

    return result
