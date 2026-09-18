"""ta_decision.parsers — JSON 结构化决策字段解析器。

从 adapter_impl.py 拆分，每个函数独立解析一个字段。
DecisionAdapter._try_parse_json 调用这些函数组合出 InvestmentDecision。
"""

from __future__ import annotations

import json
import re

from loguru import logger

from trade_krono_cli.ta_decision import (
    THESIS_TRUNCATE_LEN,
    InvestmentDecision,
    Signal,
)


def parse_json(text: str) -> InvestmentDecision | None:
    """尝试将输入解析为 JSON 结构化决策。

    支持的字段（全部可选）：
      signal, confidence, thesis, risks, expected_return, position_size,
      invalidations, entry_zone, target_price, stop_loss,
      expected_holding_period, catalysts,
      valuation_score, fundamental_score, technical_score,
      sentiment_score, capital_flow_score, macro_score
    """
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None

    if not isinstance(data, dict):
        logger.warning(
            f"[TA决策解析] JSON 解析成功但非对象类型（{type(data).__name__}），"
            f"回退到文本正则解析。",
        )
        return None

    signal = _parse_signal(data)
    confidence = _parse_confidence(data, signal)
    thesis = _parse_thesis(data)
    risks = _parse_risks(data)
    expected_return = _parse_expected_return(data)
    position_size = _parse_position_size(data)
    invalidations = _parse_invalidations(data)
    entry_zone = _parse_entry_zone(data)
    target_price, stop_loss = _parse_price_targets(data)
    holding_period = _parse_holding_period(data)
    catalysts = _parse_catalysts(data)
    scores = _parse_scores(data)

    logger.info(
        f"[TA决策解析] JSON 结构化解析成功 | signal={signal.value} confidence={confidence}",
    )
    return InvestmentDecision(
        signal=signal,
        confidence=round(confidence, 1),
        expected_return=expected_return,
        position_size=position_size,
        horizon=holding_period,
        thesis=thesis,
        risks=risks,
        invalidations=invalidations,
        entry_zone=entry_zone,
        target_price=target_price,
        stop_loss=stop_loss,
        expected_holding_period=holding_period,
        catalysts=catalysts,
        **scores,
    )


def _parse_signal(data: dict) -> Signal:
    signal_raw = data.get("signal", "HOLD")
    if isinstance(signal_raw, str):
        try:
            return Signal(signal_raw)
        except ValueError:
            try:
                return Signal(signal_raw.upper())
            except ValueError:
                logger.warning(f"[TA决策解析] 未知 signal 值 '{signal_raw}'，回退到 HOLD")
                return Signal.HOLD
    return Signal.HOLD


def _parse_confidence(data: dict, signal: Signal) -> float:
    confidence = data.get("confidence")
    if confidence is not None:
        try:
            confidence = float(confidence)
            return max(0.0, min(100.0, confidence))
        except (ValueError, TypeError):
            return 50.0
    return {Signal.BUY: 80.0, Signal.HOLD: 50.0, Signal.SELL: 30.0}[signal]


def _parse_thesis(data: dict) -> str:
    thesis = data.get("thesis", "")
    return thesis.strip()[:THESIS_TRUNCATE_LEN] if isinstance(thesis, str) else ""


def _parse_risks(data: dict) -> list[str]:
    risks_raw = data.get("risks")
    if isinstance(risks_raw, list):
        risks = [str(r).strip() for r in risks_raw if str(r).strip()]
    elif isinstance(risks_raw, str):
        risks = [r.strip() for r in re.split(r"[,\n]+", risks_raw) if r.strip()]
    else:
        risks = []
    return risks[:8]


def _parse_expected_return(data: dict) -> float | None:
    expected_return = data.get("expected_return")
    if expected_return is not None:
        try:
            return float(expected_return)
        except (ValueError, TypeError):
            return None
    return None


def _parse_position_size(data: dict) -> float | None:
    position_size = data.get("position_size")
    if position_size is not None:
        try:
            position_size = float(position_size)
            return max(-1.0, min(1.0, position_size))
        except (ValueError, TypeError):
            return None
    return None


def _parse_invalidations(data: dict) -> list[str]:
    invalidations_raw = data.get("invalidations")
    if isinstance(invalidations_raw, list):
        invalidations = [str(r).strip() for r in invalidations_raw if str(r).strip()]
    elif isinstance(invalidations_raw, str):
        invalidations = [r.strip() for r in re.split(r"[,\n]+", invalidations_raw) if r.strip()]
    else:
        invalidations = []
    return invalidations[:8]


def _parse_entry_zone(data: dict) -> list[float] | None:
    entry_zone = data.get("entry_zone")
    if isinstance(entry_zone, list) and len(entry_zone) == 2:
        try:
            return [float(entry_zone[0]), float(entry_zone[1])]
        except (ValueError, TypeError):
            return None
    return None


def _parse_price_targets(data: dict) -> tuple[float | None, float | None]:
    target_price = data.get("target_price")
    if target_price is not None:
        try:
            target_price = float(target_price)
        except (ValueError, TypeError):
            target_price = None

    stop_loss = data.get("stop_loss")
    if stop_loss is not None:
        try:
            stop_loss = float(stop_loss)
        except (ValueError, TypeError):
            stop_loss = None

    return target_price, stop_loss


def _parse_holding_period(data: dict) -> int | None:
    holding_period = data.get("expected_holding_period")
    if holding_period is not None:
        try:
            return int(holding_period)
        except (ValueError, TypeError):
            return None
    return None


def _parse_catalysts(data: dict) -> list[str]:
    catalysts_raw = data.get("catalysts")
    if isinstance(catalysts_raw, list):
        catalysts = [str(r).strip() for r in catalysts_raw if str(r).strip()]
    elif isinstance(catalysts_raw, str):
        catalysts = [r.strip() for r in re.split(r"[,\n]+", catalysts_raw) if r.strip()]
    else:
        catalysts = []
    return catalysts


def _parse_scores(data: dict) -> dict[str, float | None]:
    score_keys = [
        "valuation_score",
        "fundamental_score",
        "technical_score",
        "sentiment_score",
        "capital_flow_score",
        "macro_score",
    ]

    def _parse_score(key: str, default: float | None = None) -> float | None:
        v = data.get(key)
        if v is not None:
            try:
                f = float(v)
                return max(0.0, min(100.0, f))
            except (ValueError, TypeError):
                return default
        return default

    return {k: _parse_score(k) for k in score_keys}
