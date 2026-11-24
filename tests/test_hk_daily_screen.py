"""港股每日筛选入口测试（JSON 落盘、推送路径、参数透传）。"""

from __future__ import annotations

import json
import sys
import types
from typing import TYPE_CHECKING

import pytest

import scripts.hk_daily_screen as hk
from scripts.hk_screen.data_fetcher import HKRawData

if TYPE_CHECKING:
    from pathlib import Path


def make_raw(ticker: str, roe: float = 0.19) -> HKRawData:
    """构造一只可通过全部规则的港股原始数据（含非空 PE/ROE）。"""
    return HKRawData(
        ticker=ticker,
        name=f"测试{ticker}",
        market_cap=3e12,
        pe_ttm=11.4,
        pb=2.2,
        dividend_yield=0.0156,
        turnover_20d_avg=1e10,
        turnover_rate_12m_avg=0.5,
        listing_days=8000,
        is_hk_connect=True,
        is_hkd=True,
        is_common_stock=True,
        suspension_days_30d=0,
        has_risk_warning=False,
        debt_to_asset=0.39,
        non_gaap_profit_2y=True,
        clean_audit_2y=True,
        operating_cashflow_3y=True,
        revenue_growth_3y=True,
        gross_margin=0.56,
        avg_roe_3y=roe,
        top_shareholder_pct=0.4,
        max_drawdown_12m=0.2,
        rights_issues_12m=0,
        price=344.0,
        drawdown_from_high=0.5,
        single_day_spike_1y=0.1,
        dividend_3y=True,
        payout_ratio=0.18,
        annualized_volatility_12m=0.25,
        fcf_to_net_profit=1.34,
    )


@pytest.fixture
def patched_screen(monkeypatch: pytest.MonkeyPatch) -> None:
    """替换联网取数与环境变量加载，保持测试完全离线。"""
    monkeypatch.setattr(hk, "_load_env", lambda: None)
    monkeypatch.setattr(hk, "get_hk_stock_list_from_cache", lambda: ["hk.00700", "hk.00941"])
    monkeypatch.setattr(hk, "get_hk_connect_stocks", lambda: None)
    monkeypatch.setattr(
        hk,
        "fetch_hk_raw_data",
        lambda tickers, **kwargs: [make_raw(t) for t in tickers],
    )


class TestMain:
    def test_writes_json_with_real_pass_rate(
        self, patched_screen: None, tmp_path: Path
    ) -> None:
        json_path = tmp_path / "hk_screen.json"

        rc = hk.main(["--date", "2026-11-23", "--no-push", "--json", str(json_path)])

        assert rc == 0
        payload = json.loads(json_path.read_text(encoding="utf-8"))
        assert payload["date"] == "2026-11-23"
        assert payload["total"] == 2
        assert payload["passed"] == 2
        assert payload["pass_rate"] == 1.0
        assert payload["results"][0]["passed"] is True

    def test_date_is_passed_to_fetcher(
        self, patched_screen: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, object] = {}
        original = hk.fetch_hk_raw_data

        def _capture(tickers: list[str], **kwargs: object) -> list[HKRawData]:
            captured.update(kwargs)
            return original(tickers, **kwargs)

        monkeypatch.setattr(hk, "fetch_hk_raw_data", _capture)

        hk.main(["--date", "2026-01-02", "--no-push", "--json", str(tmp_path / "r.json")])

        assert captured.get("date") == "2026-01-02"

    def test_push_with_numeric_metrics_does_not_raise(
        self, patched_screen: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """历史 bug：推送分支使用未导入的 pd.isna → NameError 被静默吞掉。"""
        calls: list[tuple[str, str]] = []
        fake = types.ModuleType("scripts.feishu_utils")
        fake.push_feishu = lambda mode, content, title: calls.append((content, title))  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "scripts.feishu_utils", fake)

        rc = hk.main(["--json", str(tmp_path / "r.json")])

        assert rc == 0
        assert len(calls) == 1
        assert "hk.00700" in calls[0][0]
        assert "2026" in calls[0][1]

    def test_require_hk_connect_flag_is_forwarded(
        self, patched_screen: None, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict[str, object] = {}

        def _screen(results: list[object], **kwargs: object) -> list[object]:
            captured.update(kwargs)
            return []

        monkeypatch.setattr(hk, "screen_hk_stocks", _screen)

        hk.main(
            [
                "--require-hk-connect",
                "--min-pass-rate",
                "0.9",
                "--min-coverage",
                "0.7",
                "--no-push",
                "--json",
                str(tmp_path / "r.json"),
            ]
        )

        assert captured["require_hk_connect"] is True
        assert captured["min_pass_rate"] == 0.9
        assert captured["min_coverage"] == 0.7

    def test_codes_are_normalized(self, patched_screen: None, tmp_path: Path) -> None:
        hk.main(
            [
                "--codes",
                "00700,hk.00941",
                "--no-push",
                "--json",
                str(tmp_path / "r.json"),
            ]
        )

        payload = json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))
        assert [r["ticker"] for r in payload["results"]] == ["hk.00700", "hk.00941"]
