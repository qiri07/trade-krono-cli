"""trade_krono_cli.domain.risk 单元测试。

覆盖 RiskFactor / RiskAssessment：to_dict/from_dict 往返、empty 工厂、
缺失字段默认值。
"""

from __future__ import annotations

from trade_krono_cli.domain.risk import RiskAssessment, RiskFactor


class TestRiskFactor:
    def test_defaults(self) -> None:
        f = RiskFactor(name="volatility", score=45.0)
        assert f.name == "volatility"
        assert f.score == 45.0
        assert f.weight == 1.0
        assert f.detail == ""

    def test_to_dict_from_dict_roundtrip(self) -> None:
        f = RiskFactor(name="liquidity", score=70.0, weight=0.5, detail="低流动性")
        d = f.to_dict()
        assert d == {"name": "liquidity", "score": 70.0, "weight": 0.5, "detail": "低流动性"}
        f3 = RiskFactor(**d)
        assert f3 == f


class TestRiskAssessment:
    def test_defaults(self) -> None:
        a = RiskAssessment(ticker="sh.600519", eval_date="2026-08-11")
        assert a.ticker == "sh.600519"
        assert a.eval_date == "2026-08-11"
        assert a.risk_score_total == 0.0
        assert a.adjusted_expected_return is None
        assert a.risk_factors == []
        assert a.var_95 is None
        assert a.cvar_95 is None
        assert a.max_drawdown_pct is None

    def test_to_dict_from_dict_roundtrip(self) -> None:
        a = RiskAssessment(
            ticker="sh.600519",
            eval_date="2026-08-11",
            risk_score_total=65.0,
            volatility_score=40.0,
            liquidity_score=30.0,
            valuation_score=50.0,
            event_risk_score=20.0,
            market_regime_score=25.0,
            gap_risk_score=15.0,
            concentration_score=10.0,
            adjusted_expected_return=5.5,
            risk_factors=[RiskFactor(name="volatility", score=40.0, weight=1.0)],
            var_95=-10.0,
            cvar_95=-15.0,
            max_drawdown_pct=-20.0,
        )
        d = a.to_dict()
        assert d["ticker"] == "sh.600519"
        assert d["risk_score_total"] == 65.0
        assert d["adjusted_expected_return"] == 5.5
        assert len(d["risk_factors"]) == 1
        assert d["risk_factors"][0]["name"] == "volatility"

        a2 = RiskAssessment.from_dict(d)
        assert a2 == a
        assert a2.risk_factors[0].name == "volatility"
        assert a2.var_95 == -10.0

    def test_from_dict_missing_fields(self) -> None:
        a = RiskAssessment.from_dict({"ticker": "sz.000858"})
        assert a.ticker == "sz.000858"
        assert a.eval_date == ""
        assert a.risk_score_total == 0.0

    def test_empty_factory(self) -> None:
        a = RiskAssessment.empty("sh.600519", "2026-08-11")
        assert a.ticker == "sh.600519"
        assert a.eval_date == "2026-08-11"
        assert a.risk_score_total == 0.0
        assert a.risk_factors == []
