"""scripts._utils.launch_signals — 大行情启动信号计算模块。

融合五大模型（文心一言 / 腾讯元宝 / DeepSeek / 通义千问 / 智谱 ChatGLM）
对“大行情启动前夕”量能与价格特征的经验与量化结论，提供可复用的
纯计算函数，供 scripts/launch_signal_scan.py 及 combined_stock_analysis
交集汇总调用。

所有函数为纯计算，不涉及网络 I/O 和数据库读写，便于单元测试。
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from scripts.indicators import calc_ma


@dataclass(frozen=True)
class LaunchSignalParams:
    """大行情启动信号的可调阈值参数。

    所有阈值均为五模型综合后的经验初值，建议在自有历史数据上做
    样本外回测标定后再用于实盘。
    """

    # ── 蓄势质量（30 分）──
    # 地量：5 日均成交量 ≤ 250 日均量的比例
    vol_dry_ratio: float = 0.30
    # 蓄势期最短持续天数
    min_dry_days: int = 10
    # 均线粘合度阈值：(max(MA5,MA10,MA20) - min(...)) / close
    ma_squeeze_pct: float = 0.03
    # 均线粘合需连续持续的天数
    ma_squeeze_days: int = 10
    # 波动率收缩：ATR20/close 的历史分位上限
    vol_squeeze_percentile: float = 0.30
    # 突破搜索窗口：在最近 lookback_days 日内寻找突破日（而不仅限于序列最后一行）
    lookback_days: int = 5

    # ── 点火强度（25 分）──
    # 突破幅度下限：(收盘 - 前 60 日最高收盘) / 前高
    breakout_min_pct: float = 0.02
    # 突破幅度上限（超过则扣分，疑似情绪高潮）
    breakout_max_pct: float = 0.05
    # VR = V / MA(V, 20) 有效区间
    vr_min: float = 1.5
    vr_max: float = 3.0
    # 换手率健康区间
    turnover_min: float = 0.05
    turnover_max: float = 0.10
    # 换手率警惕上限（>15%~20% 需警惕派发）
    turnover_warn: float = 0.15

    # ── 确认质量（30 分，权重最高）──
    # 确认窗口天数：突破后需连续守住突破位的天数
    confirm_days: int = 3
    # 回踩缩量：回踩日量 / 突破日量，≤0.5 为佳
    pullback_vol_ratio_max: float = 0.5
    # 量能中枢上移：突破后 5 日均量 / 突破前 20 日均量
    vol_center_shift_min: float = 1.2

    # ── 评分权重 ──
    weight_buildup: float = 30.0
    weight_ignition: float = 25.0
    weight_confirm: float = 30.0
    weight_env: float = 15.0


@dataclass(frozen=True)
class LaunchSignalResult:
    """单只股票的启动信号计算结果。

    Attributes:
        ticker: 带前缀 ticker，如 sh.600519。
        signal: 是否触发启动信号（未被 kill-switch 一票否决且总分达标）。
        score: 综合得分（0~100）。
        reasons: 触发的正向理由列表。
        kill_switch: 被触发的证伪条件列表（非空时 signal 一定为 False）。
        metrics: 关键指标快照，便于落盘与展示。
    """

    ticker: str
    signal: bool
    score: float
    reasons: list[str]
    kill_switch: list[str]
    metrics: dict[str, object]


def compute_obv(close: pd.Series, volume: pd.Series) -> pd.Series:
    """计算能量潮（OBV）序列。

    OBV_t = OBV_{t-1} + Volume_t（收涨），-Volume_t（收跌），不变（平收）。

    Parameters
    ----------
    close : pd.Series
        收盘价序列。
    volume : pd.Series
        成交量序列，与 close 等长且索引对齐。

    Returns:
        与输入等长的 OBV 序列，首值为 0。
    """
    direction = close.diff().apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
    return (direction * volume).cumsum()


def compute_atr(
    high: pd.Series, low: pd.Series, close: pd.Series, window: int = 20
) -> pd.Series:
    """计算平均真实波幅（ATR）。

    TR = max(H - L, |H - Ref(C, 1)|, |L - Ref(C, 1)|)
    ATR = MA(TR, window)

    Parameters
    ----------
    high : pd.Series
        最高价序列。
    low : pd.Series
        最低价序列。
    close : pd.Series
        收盘价序列。
    window : int
        ATR 移动平均窗口，默认 20。

    Returns:
        与输入等长的 ATR 序列。
    """
    prev_close = close.shift(1)
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(window).mean()


def _consecutive_true_count(mask: pd.Series) -> int:
    """计算布尔序列从末尾开始的连续 True 天数。"""
    count = 0
    for val in mask.iloc[::-1]:
        if bool(val):
            count += 1
        else:
            break
    return count


def check_kill_switch(
    close: pd.Series,
    high: pd.Series,
    low: pd.Series,
    volume: pd.Series,
    params: LaunchSignalParams,
    breakout_price: float,
    breakout_idx: int,
) -> list[str]:
    """检查 Kill-Switch 证伪条件（一票否决）。

    出现任一条件即判定信号失效。

    Parameters
    ----------
    close : pd.Series
        收盘价序列。
    high : pd.Series
        最高价序列。
    low : pd.Series
        最低价序列。
    volume : pd.Series
        成交量序列。
    params : LaunchSignalParams
        信号参数。
    breakout_price : float
        突破位价格。
    breakout_idx : int
        突破日在序列中的位置索引。

    Returns:
        被触发的证伪条件描述列表，空列表表示未触发任何 kill-switch。
    """
    triggered: list[str] = []

    n = len(close)
    # 突破后 3 日内跌回突破位（最可能的结局）
    lookback = min(params.confirm_days, n - breakout_idx - 1)
    for k in range(1, lookback + 1):
        if close.iloc[breakout_idx + k] < breakout_price:
            triggered.append("突破后 3 日内跌回突破位（Kill-Switch）")
            break

    # 回踩放量而非缩量（在 breakout_idx 之后寻找回踩日）
    vol_ma20 = calc_ma(volume, 20)
    # 找突破后首个低于突破阳线最低价的回踩日
    breakout_low = low.iloc[breakout_idx]
    pullback_idx: int | None = None
    for k in range(1, min(params.confirm_days + 3, n - breakout_idx - 1)):
        if low.iloc[breakout_idx + k] <= breakout_low:
            pullback_idx = breakout_idx + k
            break
    if pullback_idx is not None:
        if volume.iloc[pullback_idx] > volume.iloc[breakout_idx]:
            triggered.append("回踩放量而非缩量（出货而非洗盘，Kill-Switch）")

    # 量能次日即萎缩至均量线下（单日脉冲）
    if breakout_idx + 1 < n:
        v20 = vol_ma20.iloc[breakout_idx + 1]
        if not pd.isna(v20) and volume.iloc[breakout_idx + 1] < v20:
            triggered.append("突破次日量能萎缩至均量线下（单日脉冲，Kill-Switch）")

    # 换手率暴增而价格滞涨（>warn 且当日收阴，缺流通股本时以价量关系近似）
    turnover = volume.iloc[breakout_idx] / max(close.iloc[breakout_idx], 1e-9)
    if (
        turnover > params.turnover_warn
        and breakout_idx > 0
        and close.iloc[breakout_idx] <= close.iloc[breakout_idx - 1]
    ):
        triggered.append("换手率暴增而价格滞涨（Kill-Switch）")

    return triggered


def check_buildup_quality(
    close: pd.Series,
    high: pd.Series,
    low: pd.Series,
    volume: pd.Series,
    params: LaunchSignalParams,
) -> tuple[float, list[str], dict[str, object]]:
    """检查蓄势质量（30 分）。

    包括：均线粘合、波动率收缩、蓄势期缩量、下跌缩量上涨放量。

    Parameters
    ----------
    close : pd.Series
        收盘价序列。
    high : pd.Series
        最高价序列。
    low : pd.Series
        最低价序列。
    volume : pd.Series
        成交量序列。
    params : LaunchSignalParams
        信号参数。

    Returns:
        (得分, 理由列表, 指标明细)。
    """
    score = 0.0
    reasons: list[str] = []
    metrics: dict[str, object] = {}

    n = len(close)
    if n < max(params.min_dry_days, params.ma_squeeze_days) + 60:
        return 0.0, ["数据长度不足，无法评估蓄势质量"], metrics

    # 1. 均线粘合（MA5/MA10/MA20 极差 / close ≤ pct，连续 min_days 天）
    ma5 = calc_ma(close, 5)
    ma10 = calc_ma(close, 10)
    ma20 = calc_ma(close, 20)
    ma_max = pd.concat([ma5, ma10, ma20], axis=1).max(axis=1)
    ma_min = pd.concat([ma5, ma10, ma20], axis=1).min(axis=1)
    squeeze_mask = (ma_max - ma_min) / close <= params.ma_squeeze_pct
    squeeze_days = _consecutive_true_count(squeeze_mask)
    ma_ok = squeeze_days >= params.ma_squeeze_days
    metrics["ma_squeeze_days"] = squeeze_days
    metrics["ma_squeeze_pct"] = float(((ma_max - ma_min) / close).iloc[-1])
    if ma_ok:
        score += params.weight_buildup * 0.4
        reasons.append(f"均线粘合 {squeeze_days} 天（极差/收盘 ≤ {params.ma_squeeze_pct:.0%}）")
    else:
        reasons.append(f"均线未粘合（仅 {squeeze_days} 天，需 ≥ {params.ma_squeeze_days} 天）")

    # 2. 波动率收缩：ATR20/close 的历史分位 ≤ vol_squeeze_percentile
    atr = compute_atr(high, low, close, 20)
    atr_ratio = atr / close
    # 以过去 250 日为基准计算分位
    hist = atr_ratio.dropna()
    if len(hist) >= 50:
        cur = float(atr_ratio.iloc[-1])
        pct_rank = float((hist <= cur).mean())
        metrics["atr_percentile"] = pct_rank
        if pct_rank <= params.vol_squeeze_percentile:
            score += params.weight_buildup * 0.3
            reasons.append(f"波动率收缩（ATR20 分位 {pct_rank:.0%} ≤ {params.vol_squeeze_percentile:.0%}）")
        else:
            reasons.append(f"波动率未收缩（ATR20 分位 {pct_rank:.0%}）")
    else:
        metrics["atr_percentile"] = None
        reasons.append("ATR 历史数据不足，跳过波动率收缩评分")

    # 3. 蓄势期缩量：近 min_dry_days 日均量 ≤ 60 日均量 * vol_dry_ratio
    vol_ma5 = calc_ma(volume, 5)
    vol_ma60 = calc_ma(volume, 60)
    recent_vol = float(vol_ma5.iloc[-params.min_dry_days:].mean())
    ref_vol = float(vol_ma60.iloc[-1]) if not pd.isna(vol_ma60.iloc[-1]) else 0.0
    if ref_vol > 0:
        vol_dry_ok = recent_vol <= ref_vol * params.vol_dry_ratio
        metrics["vol_dry_ratio"] = recent_vol / ref_vol
        if vol_dry_ok:
            score += params.weight_buildup * 0.3
            reasons.append(f"蓄势期缩量（近 {params.min_dry_days} 日均量 / 60 日均量 = {recent_vol / ref_vol:.2f} ≤ {params.vol_dry_ratio:.2f}）")
        else:
            reasons.append(f"蓄势期未充分缩量（{recent_vol / ref_vol:.2f} > {params.vol_dry_ratio:.2f}）")
    else:
        metrics["vol_dry_ratio"] = None
        reasons.append("60 日均量无效，跳过缩量评分")

    return score, reasons, metrics


def check_ignition_strength(
    close: pd.Series,
    high: pd.Series,
    low: pd.Series,
    volume: pd.Series,
    params: LaunchSignalParams,
    breakout_idx: int | None = None,
) -> tuple[float, list[str], dict[str, object], float | None, int | None]:
    """检查点火强度（25 分）。

    包括：突破幅度、VR 倍数、换手率、K线形态。

    Parameters
    ----------
    close : pd.Series
        收盘价序列。
    high : pd.Series
        最高价序列。
    low : pd.Series
        最低价序列。
    volume : pd.Series
        成交量序列。
    params : LaunchSignalParams
        信号参数。
    breakout_idx : int | None
        突破日在序列中的索引。None 时使用序列最后一行（兼容旧行为）。

    Returns:
        (得分, 理由列表, 指标明细, 突破价, 突破日索引)。若未突破，突破价/索引为 None。
    """
    score = 0.0
    reasons: list[str] = []
    metrics: dict[str, object] = {}

    n = len(close)
    if n < 65:
        return 0.0, ["数据长度不足（<65 日），无法评估点火强度"], metrics, None, None

    vol_ma20 = calc_ma(volume, 20)

    # 确定评估位置：若指定 breakout_idx 则在该日评估，否则用序列最后一行
    idx = breakout_idx if breakout_idx is not None else n - 1

    # 前 60 日最高收盘价（不含当日，在评估位置处）
    prev_high60 = close.shift(1).rolling(60).max()
    cur_close = float(close.iloc[idx])
    prev_high = float(prev_high60.iloc[idx])

    if pd.isna(prev_high) or prev_high <= 0:
        return 0.0, ["前 60 日最高价无效"], metrics, None, None

    breakout_pct = (cur_close - prev_high) / prev_high
    metrics["breakout_pct"] = breakout_pct
    metrics["prev_high60"] = prev_high

    # 突破判定：收盘价突破前 60 日最高 ≥ breakout_min_pct
    is_breakout = breakout_pct >= params.breakout_min_pct
    if not is_breakout:
        reasons.append(f"未突破前 60 日高点（突破幅度 {breakout_pct:.2%} < {params.breakout_min_pct:.0%}）")
        return score, reasons, metrics, None, None

    # 突破幅度在 [min, max] 区间内得高分，>max 扣分
    if breakout_pct <= params.breakout_max_pct:
        score += params.weight_ignition * 0.4
        reasons.append(f"突破幅度 {breakout_pct:.2%}（在 {params.breakout_min_pct:.0%}~{params.breakout_max_pct:.0%} 区间）")
    else:
        score += params.weight_ignition * 0.15
        reasons.append(f"突破幅度 {breakout_pct:.2%} 超过 {params.breakout_max_pct:.0%}，疑似情绪高潮，扣分")

    # K 线形态：当日收阳、上影线短
    # 这里用 close > 前日 close 判断收阳
    is_yang = cur_close > float(close.iloc[idx - 1])
    if is_yang:
        score += params.weight_ignition * 0.2
        reasons.append("当日收阳")
    else:
        reasons.append("当日收阴")

    # VR = V / MA(V, 20)
    cur_vol = float(volume.iloc[idx])
    v20 = float(vol_ma20.iloc[idx])
    vr = cur_vol / v20 if v20 > 0 else 0.0
    metrics["vr"] = vr
    if params.vr_min <= vr <= params.vr_max:
        score += params.weight_ignition * 0.25
        reasons.append(f"VR {vr:.2f}（在 {params.vr_min}~{params.vr_max} 有效区间）")
    elif vr > params.vr_max:
        score += params.weight_ignition * 0.1
        reasons.append(f"VR {vr:.2f} > {params.vr_max}，极端放量需警惕")
    else:
        reasons.append(f"VR {vr:.2f} < {params.vr_min}，放量不足")

    # 换手率（近似：成交量 / 收盘价，实际应除以流通股本，此处仅作相对参考）
    turnover_proxy = cur_vol / cur_close if cur_close > 0 else 0.0
    metrics["turnover_proxy"] = turnover_proxy
    if params.turnover_min <= turnover_proxy <= params.turnover_max:
        score += params.weight_ignition * 0.15
        reasons.append(f"换手率代理值 {turnover_proxy:.2%}（健康区间）")
    elif turnover_proxy > params.turnover_warn:
        reasons.append(f"换手率代理值 {turnover_proxy:.2%} > {params.turnover_warn:.0%}，警惕派发")
    else:
        reasons.append(f"换手率代理值 {turnover_proxy:.2%} < {params.turnover_min:.0%}，量能偏小")

    return score, reasons, metrics, cur_close, idx


def check_confirmation_quality(
    close: pd.Series,
    volume: pd.Series,
    params: LaunchSignalParams,
    breakout_price: float,
    breakout_idx: int,
) -> tuple[float, list[str], dict[str, object]]:
    """检查确认质量（30 分，权重最高）。

    包括：突破后 3-5 日守住突破位、回踩缩量、量能中枢上移、均线多头排列。

    Parameters
    ----------
    close : pd.Series
        收盘价序列。
    volume : pd.Series
        成交量序列。
    params : LaunchSignalParams
        信号参数。
    breakout_price : float
        突破位价格。
    breakout_idx : int
        突破日在序列中的索引。

    Returns:
        (得分, 理由列表, 指标明细)。
    """
    score = 0.0
    reasons: list[str] = []
    metrics: dict[str, object] = {}

    n = len(close)
    if breakout_idx is None or breakout_idx >= n - 1:
        return 0.0, ["无有效突破日，跳过确认质量评估"], metrics

    # 1. 突破后 confirm_days 日收盘价均守住突破位
    confirm_ok = True
    confirm_count = 0
    for k in range(1, params.confirm_days + 1):
        if breakout_idx + k >= n:
            break
        if close.iloc[breakout_idx + k] >= breakout_price:
            confirm_count += 1
        else:
            confirm_ok = False
            break
    metrics["confirm_days_ok"] = confirm_count
    if confirm_ok and confirm_count >= params.confirm_days:
        score += params.weight_confirm * 0.4
        reasons.append(f"突破后 {confirm_count} 日均守住突破位")
    else:
        reasons.append(f"突破后仅 {confirm_count} 日守住突破位（需 ≥ {params.confirm_days} 日）")

    # 2. 回踩缩量：寻找突破后首个回踩日，量 ≤ 突破日量 * 0.5
    breakout_vol = float(volume.iloc[breakout_idx])
    pullback_found = False
    pullback_vol_ratio = 1.0
    for k in range(1, min(params.confirm_days + 3, n - breakout_idx - 1)):
        if close.iloc[breakout_idx + k] < breakout_price:
            pullback_found = True
            pullback_vol_ratio = float(volume.iloc[breakout_idx + k]) / breakout_vol if breakout_vol > 0 else 1.0
            break
    metrics["pullback_vol_ratio"] = pullback_vol_ratio if pullback_found else None
    if pullback_found and pullback_vol_ratio <= params.pullback_vol_ratio_max:
        score += params.weight_confirm * 0.3
        reasons.append(f"回踩缩量（回踩量/突破量 = {pullback_vol_ratio:.2f} ≤ {params.pullback_vol_ratio_max:.0%}）")
    elif pullback_found:
        reasons.append(f"回踩未缩量（{pullback_vol_ratio:.2f} > {params.pullback_vol_ratio_max:.0%}）")
    else:
        reasons.append("未出现明显回踩，暂不扣分")

    # 3. 量能中枢上移：突破后 5 日均量 ≥ 突破前 20 日均量 * 1.2
    post_vol = float(volume.iloc[breakout_idx + 1 : breakout_idx + 6].mean()) if n > breakout_idx + 5 else 0.0
    pre_vol = float(volume.iloc[max(0, breakout_idx - 20) : breakout_idx].mean()) if breakout_idx > 0 else 0.0
    if pre_vol > 0 and post_vol > 0:
        vol_shift = post_vol / pre_vol
        metrics["vol_center_shift"] = vol_shift
        if vol_shift >= params.vol_center_shift_min:
            score += params.weight_confirm * 0.2
            reasons.append(f"量能中枢上移（突破后 5 日均量 / 突破前 20 日均量 = {vol_shift:.2f} ≥ {params.vol_center_shift_min:.1f}）")
        else:
            reasons.append(f"量能中枢未上移（{vol_shift:.2f} < {params.vol_center_shift_min:.1f}）")
    else:
        metrics["vol_center_shift"] = None
        reasons.append("量能数据不足，跳过中枢上移评分")

    # 4. 均线多头排列（MA5>MA10>MA20>MA60）且 MA60 向上
    ma5 = calc_ma(close, 5)
    ma10 = calc_ma(close, 10)
    ma20 = calc_ma(close, 20)
    ma60 = calc_ma(close, 60)
    try:
        px = float(close.iloc[-1])
        m5 = float(ma5.iloc[-1])
        m10 = float(ma10.iloc[-1])
        m20 = float(ma20.iloc[-1])
        m60 = float(ma60.iloc[-1])
        m60_prev = float(ma60.iloc[-2])
        if px > m5 > m10 > m20 > m60 and m60 > m60_prev:
            score += params.weight_confirm * 0.1
            reasons.append("均线多头排列且 MA60 向上")
        else:
            reasons.append("均线未形成多头排列或 MA60 未向上")
    except (TypeError, ValueError):
        reasons.append("均线数据不足，跳过多头排列评分")

    return score, reasons, metrics


def check_environment_logic(
    close: pd.Series,
    volume: pd.Series,
    params: LaunchSignalParams,
    board_codes: set[str] | None = None,
) -> tuple[float, list[str], dict[str, object]]:
    """检查环境与逻辑（15 分）。

    包括：板块共振（若传入 board_codes）、大盘环境（简化为近期趋势）。

    Parameters
    ----------
    close : pd.Series
        收盘价序列。
    volume : pd.Series
        成交量序列。
    params : LaunchSignalParams
        信号参数。
    board_codes : set[str] | None
        同板块其他已触发启动信号的 ticker 集合，用于判断板块共振。

    Returns:
        (得分, 理由列表, 指标明细)。
    """
    score = 0.0
    reasons: list[str] = []
    metrics: dict[str, object] = {}

    # 板块共振：同板块 ≥2 只同步启动
    board_count = len(board_codes) if board_codes else 0
    metrics["board_resonance_count"] = board_count
    if board_count >= 2:
        score += params.weight_env * 0.6
        reasons.append(f"板块共振（同板块 {board_count} 只同步启动）")
    elif board_count == 1:
        score += params.weight_env * 0.2
        reasons.append("板块仅本票启动，孤岛风险")
    else:
        reasons.append("无板块共振信息")

    # 大盘环境：近 20 日均线向上（简化版，实际可叠加指数数据）
    if len(close) >= 21:
        ma20 = calc_ma(close, 20)
        m60 = calc_ma(close, 60)
        try:
            if float(ma20.iloc[-1]) > float(m60.iloc[-1]):
                score += params.weight_env * 0.4
                reasons.append("大盘/个股 MA20 > MA60，环境偏强")
            else:
                reasons.append("MA20 ≤ MA60，环境偏弱")
        except (TypeError, ValueError):
            reasons.append("均线数据不足，跳过环境趋势评分")
    else:
        reasons.append("数据长度不足，跳过环境趋势评分")

    return score, reasons, metrics


def _find_recent_breakout(
    close: pd.Series, params: LaunchSignalParams
) -> tuple[float | None, int | None]:
    """在最近 lookback_days 日内寻找突破日：收盘价突破前 60 日高 ≥ breakout_min_pct。

    从最近交易日往前扫描，返回第一个（最近）满足突破条件的日子，用于后续
    的 Kill-Switch / 确认质量评估。若未发现突破，返回 (None, None)。

    Parameters
    ----------
    close : pd.Series
        收盘价序列（按时间升序，最新在最后）。
    params : LaunchSignalParams
        信号参数。

    Returns:
        (breakout_price, breakout_idx)，无突破时为 (None, None)。
    """
    n = len(close)
    if n < 65:
        return None, None
    prev_high60 = close.shift(1).rolling(60).max()
    start = max(60, n - params.lookback_days)
    for idx in range(n - 1, start - 1, -1):
        cur_close = float(close.iloc[idx])
        prev_high = float(prev_high60.iloc[idx])
        if pd.isna(prev_high) or prev_high <= 0:
            continue
        if cur_close >= prev_high * (1 + params.breakout_min_pct):
            return cur_close, idx
    return None, None


def compute_launch_signal(
    ticker: str,
    close: pd.Series,
    high: pd.Series,
    low: pd.Series,
    volume: pd.Series,
    params: LaunchSignalParams | None = None,
    board_codes: set[str] | None = None,
    min_score: float = 60.0,
) -> LaunchSignalResult:
    """计算单只股票的大行情启动信号。

    融合五模型结论：蓄势质量 + 点火强度 + 确认质量 + 环境与逻辑，
    先通过 Kill-Switch 证伪，再按加权打分判定是否触发信号。

    突破日不再限定在序列最后一行，而是在最近 ``lookback_days`` 日内查找
   （见 :func:`_find_recent_breakout`），点火/确认/Kill-Switch 均以该
    ``breakout_idx`` 为基准评估，因而历史上的已确认启动也能被打分。

    Parameters
    ----------
    ticker : str
        带前缀 ticker，如 sh.600519。
    close : pd.Series
        收盘价序列（需按时间升序，最新在最后）。
    high : pd.Series
        最高价序列。
    low : pd.Series
        最低价序列。
    volume : pd.Series
        成交量序列。
    params : LaunchSignalParams | None
        信号参数，None 时使用默认值。
    board_codes : set[str] | None
        同板块已触发信号的 ticker 集合，用于板块共振评分。
    min_score : float
        信号触发的最低总分（默认 60.0），由调用方/自动调参覆盖。

    Returns:
        LaunchSignalResult：包含 signal、score、reasons、kill_switch、metrics。
    """
    if params is None:
        params = LaunchSignalParams()

    kill_switch: list[str] = []
    reasons: list[str] = []
    metrics: dict[str, object] = {}

    # ── Step 0: 在最近 lookback_days 日内寻找突破日 ──
    breakout_price, breakout_idx = _find_recent_breakout(close, params)
    metrics["breakout_price"] = breakout_price
    metrics["breakout_idx"] = breakout_idx

    if breakout_price is None or breakout_idx is None:
        # 无突破：只做蓄势+环境评分，信号一定不触发
        buildup_score, buildup_reasons, buildup_metrics = check_buildup_quality(
            close, high, low, volume, params
        )
        env_score, env_reasons, env_metrics = check_environment_logic(
            close, volume, params, board_codes
        )
        total_score = buildup_score + env_score
        reasons.extend(buildup_reasons)
        reasons.extend(env_reasons)
        reasons.append(
            f"最近 {params.lookback_days} 日内未突破前 60 日高点（≥ {params.breakout_min_pct:.0%}）"
        )
        metrics.update(buildup_metrics)
        metrics.update(env_metrics)
        return LaunchSignalResult(
            ticker=ticker,
            signal=False,
            score=round(total_score, 1),
            reasons=reasons,
            kill_switch=[],
            metrics=metrics,
        )

    # ── Step 0.5: Kill-Switch 证伪（一票否决，以突破日为基准）──
    kill_switch = check_kill_switch(
        close, high, low, volume, params, breakout_price, breakout_idx
    )
    metrics["kill_switch"] = kill_switch

    # ── Step 1: 蓄势质量（30 分）──
    buildup_score, buildup_reasons, buildup_metrics = check_buildup_quality(
        close, high, low, volume, params
    )
    total_score = buildup_score
    reasons.extend(buildup_reasons)
    metrics.update(buildup_metrics)

    # ── Step 2: 点火强度（25 分，在突破日评估）──
    ignition_score, ignition_reasons, ignition_metrics, _, _ = (
        check_ignition_strength(close, high, low, volume, params, breakout_idx)
    )
    total_score += ignition_score
    reasons.extend(ignition_reasons)
    metrics.update(ignition_metrics)

    # ── Step 3: 确认质量（30 分，在突破日后评估）──
    confirm_score, confirm_reasons, confirm_metrics = check_confirmation_quality(
        close, volume, params, breakout_price, breakout_idx
    )
    total_score += confirm_score
    reasons.extend(confirm_reasons)
    metrics.update(confirm_metrics)

    # ── Step 4: 环境与逻辑（15 分）──
    env_score, env_reasons, env_metrics = check_environment_logic(
        close, volume, params, board_codes
    )
    total_score += env_score
    reasons.extend(env_reasons)
    metrics.update(env_metrics)

    # 信号触发条件：无 kill-switch 且总分 ≥ min_score
    signal = (not kill_switch) and (total_score >= min_score)

    return LaunchSignalResult(
        ticker=ticker,
        signal=signal,
        score=round(total_score, 1),
        reasons=reasons,
        kill_switch=kill_switch,
        metrics=metrics,
    )
