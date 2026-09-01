"""测试 PredictionUncertainty / KronosForecastResult 数据类。"""



class TestPredictionUncertainty:
    """PredictionUncertainty 序列化/反序列化测试。"""

    def test_to_dict(self):
        from trade_krono_cli.kronos_runner import PredictionUncertainty

        pu = PredictionUncertainty(
            expected_return=3.2,
            direction="UP",
            direction_score=0.85,
            volatility=1.23,
            path_dispersion=0.045,
            confidence_score=78.5,
            sample_count_used=5,
        )
        d = pu.to_dict()
        assert d["expected_return"] == 3.2
        assert d["direction"] == "UP"
        assert d["confidence_score"] == 78.5
        assert d["sample_count_used"] == 5

    def test_from_dict(self):
        from trade_krono_cli.kronos_runner import PredictionUncertainty

        d = {
            "expected_return": -2.1,
            "direction": "DOWN",
            "direction_score": 0.6,
            "volatility": 0.8,
            "path_dispersion": 0.02,
            "confidence_score": 55.0,
            "sample_count_used": 3,
        }
        pu = PredictionUncertainty.from_dict(d)
        assert pu.expected_return == -2.1
        assert pu.direction == "DOWN"
        assert pu.confidence_score == 55.0

    def test_from_dict_ignores_extra_fields(self):
        """多余字段应被忽略。"""
        from trade_krono_cli.kronos_runner import PredictionUncertainty

        d = {
            "expected_return": 1.0,
            "direction": "UP",
            "direction_score": 0.5,
            "volatility": 0.1,
            "path_dispersion": None,
            "confidence_score": 50.0,
            "sample_count_used": 1,
            "extra_field": "should_be_ignored",
        }
        pu = PredictionUncertainty.from_dict(d)
        assert pu.expected_return == 1.0
        assert not hasattr(pu, "extra_field") or pu.__dict__.get("extra_field") is None

    def test_percentile_fields(self):
        """PredictionDistribution 应包含 p10/p25/p50/p75/p90 分位数字段。"""
        from trade_krono_cli.kronos_runner import PredictionDistribution

        pd_obj = PredictionDistribution(
            expected_return=2.0,
            direction="UP",
            confidence_score=72.0,
            p10=1700.0,
            p25=1750.0,
            p50=1800.0,
            p75=1850.0,
            p90=1900.0,
        )
        d = pd_obj.to_dict()
        assert d["p10"] == 1700.0
        assert d["p25"] == 1750.0
        assert d["p50"] == 1800.0
        assert d["p75"] == 1850.0
        assert d["p90"] == 1900.0
        restored = PredictionDistribution.from_dict(d)
        assert restored.p50 == 1800.0
        assert restored.p90 == 1900.0

    def test_percentiles_default_to_none(self):
        """未设置百分位时应为 None。"""
        from trade_krono_cli.kronos_runner import PredictionDistribution

        pd_obj = PredictionDistribution(expected_return=1.0, direction="UP")
        assert pd_obj.p10 is None
        assert pd_obj.p50 is None
        assert pd_obj.p90 is None


class TestKronosForecastResult:
    """KronosForecastResult 序列化测试。"""

    def test_to_dict_with_uncertainty(self):
        from trade_krono_cli.kronos_runner import KronosForecastResult, PredictionUncertainty

        pu = PredictionUncertainty(expected_return=2.0, direction="UP", confidence_score=70.0)
        r = KronosForecastResult(
            ticker="sh.600519",
            eval_date="2026-08-12",
            horizon=30,
            expected_change_pct=2.0,
            direction="UP",
            prediction_uncertainty=pu,
        )
        d = r.to_dict()
        assert d["ticker"] == "sh.600519"
        assert d["expected_change_pct"] == 2.0
        assert d["prediction_uncertainty"]["confidence_score"] == 70.0

    def test_to_dict_without_uncertainty(self):
        from trade_krono_cli.kronos_runner import KronosForecastResult

        r = KronosForecastResult(
            ticker="sh.600519",
            eval_date="2026-08-12",
            horizon=30,
        )
        d = r.to_dict()
        assert d["prediction_uncertainty"] is None


