"""tests for scripts.universe_filter：统一前置股票池过滤。

覆盖：
- UniverseRules 默认阈值（市值下限 20 亿）。
- compute_kline_metrics：K 线指标计算（bars / last_date / close / 停牌 / 成交额 / 日期截断）。
- evaluate_ticker：各硬门槛判定（ST / 次新 / 停牌 / 行情滞后 / 低价 / 市值 / 流动性 / 无数据）。
- apply_universe_filter：快照缺失时回退原样返回；存在时按 6 位裸代码求交集，保留 hk.（keep_hk=True）。
"""

from __future__ import annotations

import pandas as pd
import pytest

import scripts.universe_filter as uf
from scripts.universe_filter import (
    DEFAULT_MIN_MARKET_CAP_YI,
    TickerMetrics,
    UniverseRules,
    apply_universe_filter,
    compute_kline_metrics,
    evaluate_ticker,
)

# ── TickerMetrics / UniverseRules ───────────────────────────────────────


def test_ticker_metrics_passed_empty_reasons() -> None:
    assert TickerMetrics(ticker="sh.600519", reasons=[]).passed is True


def test_ticker_metrics_passed_with_reasons() -> None:
    assert TickerMetrics(ticker="sh.600519", reasons=["st"]).passed is False


def test_universe_rules_default_market_cap_is_20() -> None:
    assert UniverseRules().min_market_cap_yi == DEFAULT_MIN_MARKET_CAP_YI == 20.0


# ── compute_kline_metrics ───────────────────────────────────────────────


def _kline_df(rows: list[dict[str, object]]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def test_compute_kline_metrics_none() -> None:
    out = compute_kline_metrics(None, "2026-11-20")
    assert out["bars"] == 0
    assert out["close"] is None
    assert out["avg_amount_20d"] is None


def test_compute_kline_metrics_basic() -> None:
    rows = [
        {"timestamps": pd.Timestamp("2026-10-26"), "close": 10.0, "volume": 1000.0, "amount": 10000.0},
        {"timestamps": pd.Timestamp("2026-10-27"), "close": 10.5, "volume": 0.0, "amount": 0.0},
        {"timestamps": pd.Timestamp("2026-10-28"), "close": 11.0, "volume": 2000.0, "amount": 22000.0},
    ]
    out = compute_kline_metrics(_kline_df(rows), "2026-11-20")
    assert out["bars"] == 3
    assert out["last_date"] == "2026-10-28"
    assert out["close"] == 11.0
    assert out["avg_amount_20d"] == pytest.approx(32000.0 / 3)
    assert out["suspension_days_30d"] == 1


def test_compute_kline_metrics_truncates_to_date() -> None:
    rows = [
        {"timestamps": pd.Timestamp("2026-11-18"), "close": 10.0, "volume": 1000.0, "amount": 10000.0},
        {"timestamps": pd.Timestamp("2026-11-21"), "close": 12.0, "volume": 1000.0, "amount": 12000.0},
    ]
    out = compute_kline_metrics(_kline_df(rows), "2026-11-20")
    assert out["bars"] == 1
    assert out["close"] == 10.0


# ── evaluate_ticker ─────────────────────────────────────────────────────


def _metrics(**kw) -> TickerMetrics:
    base: dict[str, object] = {
        "ticker": "sh.600519",
        "name": "贵州茅台",
        "bars": 200,
        "last_date": "2026-11-20",
        "close": 100.0,
        "avg_amount_20d": 100_000_000.0,
        "suspension_days_30d": 0,
        "market_cap_yi": 200.0,
        "pe_ttm": 20.0,
        "pb": 3.0,
        "turnover": 1.0,
        "listing_days": 1000,
        "reasons": [],
    }
    base.update(kw)
    return TickerMetrics(**base)  # type: ignore[arg-type]


def test_evaluate_ticker_passes() -> None:
    assert evaluate_ticker(_metrics(), "2026-11-20", UniverseRules()) == []


def test_evaluate_ticker_st() -> None:
    assert "st" in evaluate_ticker(_metrics(name="*ST 飞乐"), "2026-11-20", UniverseRules())


def test_evaluate_ticker_new_listing() -> None:
    assert "new_listing" in evaluate_ticker(
        _metrics(listing_days=10), "2026-11-20", UniverseRules()
    )


def test_evaluate_ticker_suspended() -> None:
    assert "suspended" in evaluate_ticker(
        _metrics(suspension_days_30d=10), "2026-11-20", UniverseRules()
    )


def test_evaluate_ticker_stale() -> None:
    assert "stale" in evaluate_ticker(
        _metrics(last_date="2026-11-10"), "2026-11-20", UniverseRules()
    )


def test_evaluate_ticker_low_price() -> None:
    assert "low_price" in evaluate_ticker(_metrics(close=0.5), "2026-11-20", UniverseRules())


def test_evaluate_ticker_low_market_cap() -> None:
    assert "low_market_cap" in evaluate_ticker(
        _metrics(market_cap_yi=10.0), "2026-11-20", UniverseRules()
    )


def test_evaluate_ticker_low_liquidity() -> None:
    assert "low_liquidity" in evaluate_ticker(
        _metrics(avg_amount_20d=10_000_000.0), "2026-11-20", UniverseRules()
    )


def test_evaluate_ticker_require_market_cap_missing() -> None:
    assert "low_market_cap" in evaluate_ticker(
        _metrics(market_cap_yi=None),
        "2026-11-20",
        UniverseRules(require_market_cap=True),
    )


def test_evaluate_ticker_no_data() -> None:
    assert evaluate_ticker(_metrics(bars=0, close=None), "2026-11-20", UniverseRules()) == [
        "no_data"
    ]


# ── apply_universe_filter ───────────────────────────────────────────────


def test_apply_universe_filter_missing_snapshot_returns_original(monkeypatch) -> None:
    monkeypatch.setattr(uf, "load_universe", lambda *a, **k: None)
    out = apply_universe_filter(["sh.600519", "sz.000858"], "2026-11-20", label="t")
    assert out == ["sh.600519", "sz.000858"]


def test_apply_universe_filter_keeps_universe(monkeypatch) -> None:
    fake_snapshot = {
        "date": "2026-11-20",
        "universe": ["sh.600519", "sz.000858", "sh.600036"],
        "rejected": {"st": ["sh.600001"], "low_liquidity": ["sz.000002"]},
    }
    monkeypatch.setattr(uf, "load_universe", lambda *a, **k: fake_snapshot)
    out = apply_universe_filter(
        ["sh.600519", "sz.000002", "sh.600001", "sh.600036", "hk.00700"],
        "2026-11-20",
        label="t",
        keep_hk=True,
    )
    assert out == ["sh.600519", "sh.600036", "hk.00700"]


def test_apply_universe_filter_drops_hk_when_keep_hk_false(monkeypatch) -> None:
    fake_snapshot = {"date": "2026-11-20", "universe": ["sh.600519"], "rejected": {}}
    monkeypatch.setattr(uf, "load_universe", lambda *a, **k: fake_snapshot)
    out = apply_universe_filter(
        ["sh.600519", "hk.00700"], "2026-11-20", label="t", keep_hk=False
    )
    assert out == ["sh.600519"]


def test_apply_universe_filter_matches_bare_code(monkeypatch) -> None:
    """快照里带前缀 sh.600519，传入裸码 600519 也能命中。"""
    fake_snapshot = {"date": "2026-11-20", "universe": ["sh.600519"], "rejected": {}}
    monkeypatch.setattr(uf, "load_universe", lambda *a, **k: fake_snapshot)
    out = apply_universe_filter(["600519"], "2026-11-20", label="t")
    assert out == ["600519"]
