#!/usr/bin/env python3
"""risk.concentration 测试 — 集中度风险计算。"""

from __future__ import annotations

import pytest

from trade_krono_cli.risk.concentration import (
    _extract_market_cap,
    _extract_sector,
    calc_concentration_risk,
)


class TestCalcConcentrationRisk:
    """calc_concentration_risk 主函数测试。"""

    def test_none_result(self) -> None:
        """无 TA 结果时返回默认值。"""
        result = calc_concentration_risk(None)
        assert result == 15.0

    def test_empty_reports(self) -> None:
        """空 reports 时返回默认值。"""
        from trade_krono_cli.ta_runner import StockAnalysisResult

        ta = StockAnalysisResult(
            ticker="sh.600519",
            date="2026-08-11",
            signal="BUY",
            confidence=70.0,
            reasoning="test",
            reports={},
        )
        result = calc_concentration_risk(ta)
        assert isinstance(result, float)
        assert 0 <= result <= 100

    def test_with_industry_report(self) -> None:
        """有行业报告时按行业调整风险分。"""
        from trade_krono_cli.ta_runner import StockAnalysisResult

        ta = StockAnalysisResult(
            ticker="sh.600519",
            date="2026-08-11",
            signal="BUY",
            confidence=70.0,
            reasoning="test",
            reports={"fundamental_report": "房地产行业分析，REITs 前景"},
        )
        result = calc_concentration_risk(ta)
        # real_estate sector has risk factor 15.0
        assert result == 15.0

    def test_large_cap_lower_risk(self) -> None:
        """大盘股（≥500亿）集中度风险降低。"""
        from trade_krono_cli.ta_runner import StockAnalysisResult

        ta = StockAnalysisResult(
            ticker="sh.600519",
            date="2026-08-11",
            signal="BUY",
            confidence=70.0,
            reasoning="test",
            reports={"fundamental_report": "市值 800 亿元，银行行业龙头"},
        )
        result = calc_concentration_risk(ta)
        # bank sector base=8.0, large cap → -5.0 = 3.0
        assert result == pytest.approx(3.0, abs=0.5)
        """小盘股（<50亿）集中度风险升高。"""
        from trade_krono_cli.ta_runner import StockAnalysisResult

        ta = StockAnalysisResult(
            ticker="sh.600519",
            date="2026-08-11",
            signal="BUY",
            confidence=70.0,
            reasoning="test",
            reports={"fundamental_report": "市值 30 亿元，科技行业"},
        )
        result = calc_concentration_risk(ta)
        # tech sector base=10.0, small cap → +8.0 = 18.0
        assert result == pytest.approx(18.0, abs=0.5)


class TestExtractSector:
    """_extract_sector 辅助函数测试。"""

    def test_bank_keyword(self) -> None:
        report = "银行板块分析：招商银行业绩稳定"
        assert _extract_sector({"fundamental_report": report}) == "bank"

    def test_tech_keyword(self) -> None:
        report = "科技行业：AI 芯片需求旺盛"
        assert _extract_sector({"fundamental_report": report}) == "tech"

    def test_no_sector_keyword(self) -> None:
        report = "该公司主营业务为电子产品制造"
        result = _extract_sector({"fundamental_report": report})
        assert result is None

    def test_empty_report(self) -> None:
        assert _extract_sector({}) is None
        assert _extract_sector({"fundamental_report": ""}) is None

    def test_prefer_fundamental_over_analysis(self) -> None:
        """fundamental_report 优先于 analysis_report。"""
        fundamental = "房地产行业分析"
        analysis = "科技板块"
        result = _extract_sector(
            {
                "fundamental_report": fundamental,
                "analysis_report": analysis,
            }
        )
        assert result == "real_estate"


class TestExtractMarketCap:
    """_extract_market_cap 辅助函数测试。"""

    def test_chinese_format(self) -> None:
        report = "总市值：500 亿元，估值合理"
        result = _extract_market_cap({"fundamental_report": report})
        assert result == pytest.approx(500.0, abs=0.1)

    def test_english_format(self) -> None:
        report = "market cap: 800 billion"
        result = _extract_market_cap({"fundamental_report": report})
        assert result == pytest.approx(800.0, abs=0.1)

    def test_no_market_cap(self) -> None:
        report = "该公司成立于2010年"
        result = _extract_market_cap({"fundamental_report": report})
        assert result is None

    def test_empty_report(self) -> None:
        assert _extract_market_cap({}) is None
        assert _extract_market_cap({"fundamental_report": ""}) is None
