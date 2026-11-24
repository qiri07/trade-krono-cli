"""港股筛选器规则判定与排序测试。"""

from __future__ import annotations

import pytest

from scripts.hk_screen.screener import (
    HKScreenResult,
    compute_quality_score,
    count_passed_rules,
    normalize_dividend_yield,
    print_screen_summary,
    screen_hk_stocks,
)


def make_result(**overrides: object) -> HKScreenResult:
    """构造一只「全部指标均满足」的港股结果，供各测试局部覆盖。"""
    base: dict[str, object] = {
        "ticker": "hk.00700",
        "name": "腾讯控股",
        "market_cap": 3.0e12,
        "pe_ttm": 11.4,
        "pb": 2.2,
        "dividend_yield": 0.0156,
        "turnover_20d_avg": 1.0e10,
        "turnover_rate_12m_avg": 0.5,
        "listing_days": 8000,
        "is_hk_connect": True,
        "is_hkd": True,
        "is_common_stock": True,
        "suspension_days_30d": 0,
        "has_risk_warning": False,
        "debt_to_asset": 0.39,
        "non_gaap_profit_2y": True,
        "clean_audit_2y": True,
        "operating_cashflow_3y": True,
        "revenue_growth_3y": True,
        "gross_margin": 0.56,
        "avg_roe_3y": 0.19,
        "top_shareholder_pct": 0.4,
        "max_drawdown_12m": 0.2,
        "rights_issues_12m": 0,
        "price": 344.0,
        "drawdown_from_high": 0.5,
        "single_day_spike_1y": 0.1,
        "dividend_3y": True,
        "payout_ratio": 0.18,
        "annualized_volatility_12m": 0.25,
        "fcf_to_net_profit": 1.34,
    }
    base.update(overrides)
    return HKScreenResult(**base)  # type: ignore[arg-type]


class TestCountPassedRules:
    def test_all_known_and_satisfied_passes_everything(self) -> None:
        verdict = count_passed_rules(make_result(), require_hk_connect=True)

        assert verdict.evaluated == 22
        assert verdict.unknown == 0
        assert verdict.passed == 22
        assert verdict.fail_reasons == []

    def test_unknown_metric_is_not_counted_as_failure(self) -> None:
        verdict = count_passed_rules(make_result(debt_to_asset=None))

        assert verdict.unknown == 1
        assert verdict.evaluated == 20
        assert verdict.passed == 20
        assert "近2年审计意见非标准无保留" not in verdict.fail_reasons

    def test_explicit_false_is_failure(self) -> None:
        verdict = count_passed_rules(make_result(clean_audit_2y=False))

        assert verdict.evaluated == 21
        assert verdict.passed == 20
        assert "近2年审计意见非标准无保留" in verdict.fail_reasons

    def test_hk_connect_rule_skipped_when_not_required(self) -> None:
        """未强制港股通时，该规则不应计入，避免白送一条「通过」。"""
        verdict_unknown = count_passed_rules(make_result(is_hk_connect=None))
        verdict_required = count_passed_rules(
            make_result(is_hk_connect=None), require_hk_connect=True
        )

        assert verdict_unknown.evaluated == 21
        assert verdict_unknown.unknown == 0
        assert verdict_required.unknown == 1
        assert verdict_required.evaluated == 21

    def test_hk_connect_false_fails_when_required(self) -> None:
        verdict = count_passed_rules(make_result(is_hk_connect=False), require_hk_connect=True)

        assert "不在港股通清单（或清单获取失败）" in verdict.fail_reasons

    def test_coverage_reflects_data_completeness(self) -> None:
        verdict = count_passed_rules(
            make_result(clean_audit_2y=None, top_shareholder_pct=None), require_hk_connect=True
        )

        assert verdict.unknown == 2
        assert verdict.evaluated == 20
        assert verdict.coverage == pytest.approx(20 / 22)


class TestScreenHkStocks:
    def test_min_coverage_blocks_data_starved_stock(self) -> None:
        """数据大面积缺失时即便无失败规则也不应通过（fail-closed）。"""
        starved = make_result(
            debt_to_asset=None,
            gross_margin=None,
            avg_roe_3y=None,
            operating_cashflow_3y=None,
            revenue_growth_3y=None,
            non_gaap_profit_2y=None,
        )

        passed = screen_hk_stocks([starved], min_pass_rate=1.0, min_coverage=0.9)

        assert passed == []
        assert starved.passed is False
        assert starved.coverage < 0.9

    def test_low_coverage_rule_disabled_allows_pass(self) -> None:
        starved = make_result(debt_to_asset=None, gross_margin=None)

        passed = screen_hk_stocks([starved], min_pass_rate=1.0, min_coverage=0.0)

        assert [r.ticker for r in passed] == ["hk.00700"]

    def test_failed_stock_is_excluded_and_annotated(self) -> None:
        ok = make_result()
        bad = make_result(ticker="hk.00941", avg_roe_3y=0.05)

        passed = screen_hk_stocks([ok, bad], min_pass_rate=1.0)

        assert [r.ticker for r in passed] == ["hk.00700"]
        assert bad.passed is False
        assert any("ROE" in reason for reason in bad.fail_reasons)
        assert bad.total_rules == 21
        assert bad.pass_count == 20

    def test_results_sorted_by_quality_score(self) -> None:
        cheap = make_result(ticker="hk.00001", pe_ttm=8.0)
        pricey = make_result(ticker="hk.00002", pe_ttm=22.0)

        passed = screen_hk_stocks([pricey, cheap], min_pass_rate=1.0)

        assert [r.ticker for r in passed] == ["hk.00001", "hk.00002"]

    def test_min_pass_rate_tolerates_partial_failures(self) -> None:
        relaxed = make_result(clean_audit_2y=False, top_shareholder_pct=None)

        assert screen_hk_stocks([relaxed], min_pass_rate=1.0) == []
        assert [r.ticker for r in screen_hk_stocks([relaxed], min_pass_rate=0.9)] == ["hk.00700"]


class TestQualityScore:
    def test_score_is_bounded(self) -> None:
        assert 0.0 <= compute_quality_score(make_result()) <= 100.0

    def test_no_free_points_for_missing_pe(self) -> None:
        """PE 缺失不应无条件加分（旧实现曾白送 15 分）。"""
        with_pe = compute_quality_score(make_result(pe_ttm=12.0))
        without_pe = compute_quality_score(make_result(pe_ttm=None))

        assert with_pe - without_pe == pytest.approx(20.0)

    def test_graded_pe_buckets(self) -> None:
        assert compute_quality_score(make_result(pe_ttm=12.0)) > compute_quality_score(
            make_result(pe_ttm=20.0)
        )
        assert compute_quality_score(make_result(pe_ttm=20.0)) > compute_quality_score(
            make_result(pe_ttm=40.0)
        )

    def test_dividend_yield_percent_and_ratio_are_equivalent(self) -> None:
        as_ratio = compute_quality_score(make_result(dividend_yield=0.05))
        as_percent = compute_quality_score(make_result(dividend_yield=5.0))

        assert as_ratio == as_percent

    def test_dividend_three_year_bonus(self) -> None:
        assert compute_quality_score(make_result(dividend_3y=True)) > compute_quality_score(
            make_result(dividend_3y=False)
        )


class TestNormalizeDividendYield:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (0.04, 0.04),
            (4.0, 0.04),
            (1.0, 1.0),
            (None, None),
        ],
    )
    def test_normalize(self, raw: float | None, expected: float | None) -> None:
        assert normalize_dividend_yield(raw) == expected


class TestPrintScreenSummary:
    def test_summary_reports_real_pass_rate(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """入参为全量结果时，通过率不应恒为 100%。"""
        import scripts.hk_screen.screener as screener

        messages: list[str] = []
        monkeypatch.setattr(screener.logger, "info", lambda msg, *a, **kw: messages.append(msg))

        results = [make_result(), make_result(ticker="hk.00941", avg_roe_3y=0.05)]
        screen_hk_stocks(results, min_pass_rate=1.0)
        print_screen_summary(results)

        assert any("通过率: 50.0%" in msg for msg in messages)
        assert any("数据完整度" in msg for msg in messages)
