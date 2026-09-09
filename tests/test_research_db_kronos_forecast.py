"""测试 ResearchDatabase — Kronos Forecast 表 CRUD。"""

from __future__ import annotations

import pytest

from trade_krono_cli.research_db import ResearchDatabase


@pytest.fixture
def research_db(tmp_path):
    """使用临时目录创建独立的 ResearchDatabase 实例。"""
    db = tmp_path / "research.db"
    return ResearchDatabase(db_path=db)


def test_insert_kronos(research_db) -> None:
    """测试写入Kronos预测记录。"""
    from trade_krono_cli.kronos_runner import KronosForecastResult, PredictionUncertainty

    job_id = research_db.create_job("2026-08-11", ["sh.600519"])

    pu = PredictionUncertainty(
        expected_return=3.2,
        direction="UP",
        direction_score=0.8,
        confidence_score=75.0,
        sample_count_used=1,
    )

    result = KronosForecastResult(
        ticker="sh.600519",
        eval_date="2026-08-11",
        horizon=30,
        direction="UP",
        expected_change_pct=3.2,
        last_close=1780.5,
        predicted_close_final=1837.73,
        prediction_uncertainty=pu,
        confidence_band=[1750.0, 1850.0],
        error=None,
        elapsed_sec=2.5,
    )

    research_db.insert_kronos(job_id, result)

    forecasts = research_db.get_kronos_by_job(job_id)
    assert len(forecasts) == 1
    assert forecasts[0]["ticker"] == "sh.600519"
    assert forecasts[0]["direction"] == "UP"
    assert abs(forecasts[0]["expected_change"] - 3.2) < 0.01


def test_insert_kronos_no_uncertainty(research_db) -> None:
    """测试写入无不确定性数据的预测。"""
    from trade_krono_cli.kronos_runner import KronosForecastResult

    job_id = research_db.create_job("2026-08-11", ["sh.600519"])

    result = KronosForecastResult(
        ticker="sh.600519",
        eval_date="2026-08-11",
        horizon=30,
        direction="UP",
        expected_change_pct=2.0,
        last_close=100.0,
        predicted_close_final=102.0,
        prediction_uncertainty=None,
        confidence_band=None,
        error=None,
        elapsed_sec=1.5,
    )

    research_db.insert_kronos(job_id, result)

    forecasts = research_db.get_kronos_by_job(job_id)
    assert len(forecasts) == 1
    assert forecasts[0]["error"] is None


def test_get_kronos_by_job_not_found(research_db) -> None:
    """测试查询不存在的job_id。"""
    forecasts = research_db.get_kronos_by_job("nonexistent")
    assert forecasts == []


def test_insert_kronos_overwrite(research_db) -> None:
    """测试同一ticker的预测会被覆盖。"""
    from trade_krono_cli.kronos_runner import KronosForecastResult, PredictionUncertainty

    job_id = research_db.create_job("2026-08-11", ["sh.600519"])

    pu1 = PredictionUncertainty(
        expected_return=3.2,
        direction="UP",
        direction_score=0.8,
        confidence_score=75.0,
        sample_count_used=1,
    )
    result1 = KronosForecastResult(
        ticker="sh.600519",
        eval_date="2026-08-11",
        horizon=30,
        direction="UP",
        expected_change_pct=3.2,
        last_close=1780.5,
        predicted_close_final=1837.73,
        prediction_uncertainty=pu1,
        confidence_band=None,
        error=None,
        elapsed_sec=2.5,
    )

    pu2 = PredictionUncertainty(
        expected_return=-1.5,
        direction="DOWN",
        direction_score=0.6,
        confidence_score=60.0,
        sample_count_used=1,
    )
    result2 = KronosForecastResult(
        ticker="sh.600519",
        eval_date="2026-08-11",
        horizon=30,
        direction="DOWN",
        expected_change_pct=-1.5,
        last_close=1780.5,
        predicted_close_final=1753.87,
        prediction_uncertainty=pu2,
        confidence_band=None,
        error=None,
        elapsed_sec=2.5,
    )

    research_db.insert_kronos(job_id, result1)
    research_db.insert_kronos(job_id, result2)

    forecasts = research_db.get_kronos_by_job(job_id)
    assert len(forecasts) == 1
    assert forecasts[0]["direction"] == "DOWN"
    assert abs(forecasts[0]["expected_change"] - (-1.5)) < 0.01


def test_insert_kronos_with_error(research_db) -> None:
    """测试写入带错误的预测。"""
    from trade_krono_cli.kronos_runner import KronosForecastResult

    job_id = research_db.create_job("2026-08-11", ["sh.600519"])

    result = KronosForecastResult(
        ticker="sh.600519",
        eval_date="2026-08-11",
        horizon=30,
        direction="UP",
        expected_change_pct=None,
        last_close=1780.5,
        predicted_close_final=None,
        prediction_uncertainty=None,
        confidence_band=None,
        error="模型推理失败",
        elapsed_sec=0.5,
    )

    research_db.insert_kronos(job_id, result)

    forecasts = research_db.get_kronos_by_job(job_id)
    assert len(forecasts) == 1
    assert forecasts[0]["error"] == "模型推理失败"
