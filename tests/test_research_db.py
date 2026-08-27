"""测试 ResearchDatabase — 研究数据库（永久存储，无 TTL）。"""
import json
import pytest
import sqlite3
from pathlib import Path
from trade_krono_cli.research_db import ResearchDatabase
from trade_krono_cli.cache import Cache
from trade_krono_cli.version import reset_run_id_counter


@pytest.fixture
def research_db(tmp_path):
    """使用临时目录创建独立的 ResearchDatabase 实例。"""
    db = tmp_path / "research.db"
    return ResearchDatabase(db_path=db)


@pytest.fixture
def cache_only():
    """纯 Cache 实例（不触碰 research 表）。"""
    return Cache()


# ── Jobs ────────────────────────────────────────────────────────────────────

def test_create_job(research_db):
    job_id = research_db.create_job("2026-08-11", ["sh.600519", "sz.000858"])
    assert job_id is not None
    assert len(job_id) > 0

    job = research_db.get_job(job_id)
    assert job is not None
    assert job["date"] == "2026-08-11"
    assert job["n_tickers"] == 2
    assert job["n_success"] == 0
    assert set(job["tickers"]) == {"sh.600519", "sz.000858"}


def test_complete_job(research_db):
    job_id = research_db.create_job("2026-08-11", ["sh.600519"])
    research_db.complete_job(job_id, n_success=1, elapsed=12.5)

    job = research_db.get_job(job_id)
    assert job["n_success"] == 1
    assert abs(job["elapsed"] - 12.5) < 0.01


def test_list_jobs(research_db):
    j1 = research_db.create_job("2026-08-10", ["sh.600519"])
    j2 = research_db.create_job("2026-08-11", ["sz.000858", "sh.600036"])
    jobs = research_db.list_jobs()
    assert len(jobs) == 2
    # 最新在前
    assert jobs[0]["job_id"] == j2
    assert jobs[1]["job_id"] == j1


def test_list_jobs_limit(research_db):
    for i in range(5):
        research_db.create_job("2026-08-11", ["sh.600519"])
    jobs = research_db.list_jobs(limit=2)
    assert len(jobs) == 2


# ── TA Analysis ─────────────────────────────────────────────────────────────

def test_insert_ta(research_db):
    from trade_krono_cli.ta_runner import StockAnalysisResult
    from trade_krono_cli.ta_decision import InvestmentDecision, Signal

    job_id = research_db.create_job("2026-08-11", ["sh.600519"])
    result = StockAnalysisResult(
        ticker="sh.600519", date="2026-08-11",
        signal="BUY", confidence=85.0,
        reasoning="基本面良好",
        investment_decision=InvestmentDecision(
            signal=Signal.BUY, confidence=85.0,
            thesis="核心论点", risks=["风险A"],
        ),
    )
    research_db.insert_ta(job_id, result)

    records = research_db.get_ta_by_job(job_id)
    assert len(records) == 1
    assert records[0]["ticker"] == "sh.600519"
    assert records[0]["signal"] == "BUY"
    assert records[0]["confidence"] == 85.0


def test_insert_ta_with_error(research_db):
    from trade_krono_cli.ta_runner import StockAnalysisResult

    job_id = research_db.create_job("2026-08-11", ["sh.600519"])
    result = StockAnalysisResult(
        ticker="sh.600519", date="2026-08-11",
        error="Network timeout",
    )
    research_db.insert_ta(job_id, result)

    records = research_db.get_ta_by_job(job_id)
    assert records[0]["error"] == "Network timeout"


# ── Kronos Forecast ─────────────────────────────────────────────────────────

def test_insert_kronos(research_db):
    from trade_krono_cli.kronos_runner import KronosForecastResult, PredictionUncertainty

    job_id = research_db.create_job("2026-08-11", ["sh.600519"])
    result = KronosForecastResult(
        ticker="sh.600519", eval_date="2026-08-11", horizon=30,
        direction="UP", expected_change_pct=3.2,
        predicted_close_final=1837.73,
        prediction_uncertainty=PredictionUncertainty(
            expected_return=3.2, direction="UP",
            direction_score=0.72, confidence_score=72.0,
        ),
    )
    research_db.insert_kronos(job_id, result)

    records = research_db.get_kronos_by_job(job_id)
    assert len(records) == 1
    assert records[0]["direction"] == "UP"
    assert records[0]["expected_change"] == 3.2


# ── Signals ─────────────────────────────────────────────────────────────────

def test_insert_signals(research_db):
    job_id = research_db.create_job("2026-08-11", ["sh.600519"])
    merged = [
        {
            "ticker": "sh.600519", "rank": 1,
            "composite_score": 82.1,
            "ta_signal": "BUY", "ta_confidence": 80.0,
            "ta_reasoning": "基本面良好",
            "kronos_direction": "UP", "kronos_change_pct": 3.2,
            "kronos_prediction_uncertainty": {"confidence_score": 72.0},
            "ta_error": None, "kronos_error": None,
        },
    ]
    research_db.insert_signals(job_id, merged)

    records = research_db.get_signals_by_job(job_id)
    assert len(records) == 1
    assert records[0]["ticker"] == "sh.600519"
    assert records[0]["rank"] == 1
    assert abs(records[0]["composite_score"] - 82.1) < 0.01
    assert records[0]["ta_signal"] == "BUY"


# ── Decisions ───────────────────────────────────────────────────────────────

def test_insert_decision(research_db):
    from trade_krono_cli.ta_decision import InvestmentDecision, Signal

    job_id = research_db.create_job("2026-08-11", ["sh.600519"])
    decision = InvestmentDecision(
        signal=Signal.BUY, confidence=82.0,
        expected_return=12.5, position_size=0.08,
        thesis="核心论点摘要", risks=["估值风险", "政策风险"],
    )
    research_db.insert_decision(job_id, "sh.600519", decision, decision.thesis, decision.risks)

    loaded = research_db.get_decision(job_id, "sh.600519")
    assert loaded is not None
    assert loaded["decision"]["signal"] == "BUY"
    assert loaded["decision"]["confidence"] == 82.0
    assert loaded["thesis"] == "核心论点摘要"
    assert loaded["risks"] == ["估值风险", "政策风险"]


# ── Raw Reports Index ───────────────────────────────────────────────────────

def test_index_raw_report(research_db):
    import sqlite3
    job_id = research_db.create_job("2026-08-11", ["sh.600519"])
    research_db.index_raw_report(
        job_id, "sh.600519",
        "/path/to/raw/sh.600519.json",
        {"market": 2400, "fundamentals": 1800},
    )
    with sqlite3.connect(research_db._db_path) as conn:
        row = conn.execute(
            "SELECT path, reports FROM raw_reports WHERE job_id=? AND ticker=?",
            (job_id, "sh.600519"),
        ).fetchone()
    assert row is not None
    assert row[0] == "/path/to/raw/sh.600519.json"
    loaded = json.loads(row[1])
    assert loaded["market"] == 2400


# ── Stats ───────────────────────────────────────────────────────────────────

def test_stats_empty(research_db):
    stats = research_db.stats()
    assert "research_jobs" in stats
    assert "research_ta_analysis" in stats
    assert stats["research_jobs"] == 0


def test_stats_after_insert(research_db):
    job_id = research_db.create_job("2026-08-11", ["sh.600519"])
    research_db.complete_job(job_id, n_success=1, elapsed=5.0)

    stats = research_db.stats()
    assert stats["research_jobs"] == 1
    assert stats["research_ta_analysis"] == 0


# ── Query History ───────────────────────────────────────────────────────────

def test_query_history(research_db):
    # Create two jobs with signals
    j1 = research_db.create_job("2026-08-10", ["sh.600519"])
    research_db.insert_signals(j1, [{
        "ticker": "sh.600519", "rank": 1, "composite_score": 80.0,
        "ta_signal": "BUY", "ta_confidence": 80.0,
        "kronos_direction": "UP", "kronos_change_pct": 2.5,
        "ta_reasoning": "", "uncertainty": None,
        "ta_error": None, "kronos_error": None,
    }])
    research_db.complete_job(j1, n_success=1, elapsed=5.0)

    j2 = research_db.create_job("2026-08-11", ["sh.600519"])
    research_db.insert_signals(j2, [{
        "ticker": "sh.600519", "rank": 2, "composite_score": 75.0,
        "ta_signal": "HOLD", "ta_confidence": 60.0,
        "kronos_direction": "DOWN", "kronos_change_pct": -1.0,
        "ta_reasoning": "", "uncertainty": None,
        "ta_error": None, "kronos_error": None,
    }])
    research_db.complete_job(j2, n_success=1, elapsed=6.0)

    records = research_db.query_history("sh.600519")
    assert len(records) == 2
    # 最新在前
    assert records[0]["date"] == "2026-08-11"
    assert records[0]["composite_score"] == 75.0
    assert records[1]["date"] == "2026-08-10"
    assert records[1]["ta_signal"] == "BUY"


def test_query_history_no_records(research_db):
    records = research_db.query_history("sh.999999")
    assert records == []


# ── Cache vs Research Separation ────────────────────────────────────────────

def test_cache_and_research_are_separate(tmp_path):
    """Cache 操作不影响 Research 表，反之亦然。"""
    cache = Cache(db_path=tmp_path / "cache.db")
    research = ResearchDatabase(db_path=tmp_path / "cache.db")

    # 写入 cache
    cache.set_ta("sh.600519", "2026-08-11", {"signal": "BUY"})
    # 创建 research job
    job_id = research.create_job("2026-08-11", ["sh.600519"])

    # clear_all 只清除 cache，不影响 research
    cache.clear_all()
    cache_stats = cache.stats()
    assert all(v == 0 for v in cache_stats.values())

    # research 不受影响
    job = research.get_job(job_id)
    assert job is not None
    assert job["n_tickers"] == 1


def test_clear_cache_does_not_affect_research(tmp_path):
    """clear_all 后 research 数据仍在。"""
    db = tmp_path / "combined.db"
    cache = Cache(db_path=db)
    research = ResearchDatabase(db_path=db)

    research.create_job("2026-08-11", ["sh.600519"])
    cache.set_ta("sh.600519", "2026-08-11", {"test": True})

    cache.clear_all()

    # research 仍有数据
    jobs = research.list_jobs()
    assert len(jobs) == 1


# ── 版本追踪 ─────────────────────────────────────────────────────────────────

class _MockSettings:
    """模拟 Settings 对象用于版本快照测试。"""
    max_debate_rounds = 1
    max_risk_discuss_rounds = 1
    kronos_model = "kronos-base"
    kronos_tokenizer = "kronos-Tokenizer-base"
    kronos_device = "cpu"
    kronos_lookback = 400
    kronos_pred_len = 30
    kronos_sample_count = 1
    kronos_T = 1.0
    kronos_top_p = 0.9
    kronos_use_sample_confidence = False
    default_min_confidence = 55.0
    llm_provider = "deepseek"
    deep_think_llm = "deepseek-chat"
    quick_think_llm = "deepseek-chat"
    output_language = "Chinese"
    checkpoint_enabled = True


def test_create_job_with_version_snapshot(research_db):
    """create_job 传入 settings 应填充版本字段。"""
    reset_run_id_counter()
    job_id = research_db.create_job(
        "2026-08-11", ["sh.600519"],
        settings=_MockSettings(),
    )
    job = research_db.get_job(job_id)

    assert job["run_id"] is not None
    assert job["data_version"] == "baostock-2026-08-11"
    assert "kronos" in job["model_versions"]
    assert "llm" in job["model_versions"]
    assert job["prompt_version"] == "ta-v1r1-chinese-json"
    assert job["strategy_version"] == "0.1.0"
    assert len(job["config_hash"]) == 16


def test_get_run_snapshot(research_db):
    """get_run_snapshot 返回完整的版本快照。"""
    reset_run_id_counter()
    job_id = research_db.create_job(
        "2026-08-11", ["sh.600519"],
        settings=_MockSettings(),
    )
    snapshot = research_db.get_run_snapshot(job_id)

    assert snapshot is not None
    assert "run_id" in snapshot
    assert "data_version" in snapshot
    assert "model_versions" in snapshot
    assert "config_hash" in snapshot


def test_list_jobs_includes_versions(research_db):
    """list_jobs 应包含版本摘要。"""
    reset_run_id_counter()
    research_db.create_job("2026-08-11", ["sh.600519"], settings=_MockSettings())
    research_db.create_job("2026-08-10", ["sz.000858"], settings=_MockSettings())

    jobs = research_db.list_jobs()
    assert len(jobs) == 2
    # 每个 job 都应有关键版本字段
    for j in jobs:
        assert j["data_version"] is not None
        assert j["config_hash"] is not None
        assert j["strategy_version"] is not None


def test_schema_migration_old_db(tmp_path):
    """从旧 schema（无版本列）自动迁移。"""
    db = tmp_path / "old.db"

    # 先创建旧 schema（无版本列）
    with sqlite3.connect(db) as conn:
        conn.executescript("""
            CREATE TABLE jobs (
                job_id TEXT PRIMARY KEY,
                run_at REAL NOT NULL,
                date TEXT NOT NULL,
                tickers TEXT NOT NULL,
                n_tickers INTEGER NOT NULL,
                n_success INTEGER NOT NULL,
                elapsed REAL NOT NULL,
                notes TEXT
            );
            CREATE TABLE ta_analysis (
                job_id TEXT NOT NULL, ticker TEXT NOT NULL,
                signal TEXT, confidence REAL, thesis TEXT,
                risks TEXT, error TEXT, elapsed REAL,
                PRIMARY KEY (job_id, ticker)
            );
            CREATE TABLE kronos_forecast (
                job_id TEXT NOT NULL, ticker TEXT NOT NULL,
                direction TEXT, expected_change REAL,
                predicted_close REAL, confidence_band TEXT,
                uncertainty TEXT, error TEXT, elapsed REAL,
                PRIMARY KEY (job_id, ticker)
            );
            CREATE TABLE signals (
                job_id TEXT NOT NULL, ticker TEXT NOT NULL,
                rank INTEGER, composite_score REAL,
                ta_signal TEXT, ta_confidence REAL,
                ta_reasoning TEXT, kronos_direction TEXT,
                kronos_change REAL, uncertainty TEXT,
                ta_error TEXT, kronos_error TEXT,
                PRIMARY KEY (job_id, ticker)
            );
            CREATE TABLE decisions (
                job_id TEXT NOT NULL, ticker TEXT NOT NULL,
                decision_json TEXT NOT NULL, thesis TEXT,
                risks TEXT, PRIMARY KEY (job_id, ticker)
            );
            CREATE TABLE raw_reports (
                job_id TEXT NOT NULL, ticker TEXT NOT NULL,
                path TEXT NOT NULL, reports TEXT,
                PRIMARY KEY (job_id, ticker)
            );
            CREATE TABLE backtest_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL, strategy TEXT NOT NULL,
                symbols TEXT, start_date TEXT, end_date TEXT,
                results TEXT, created_at REAL NOT NULL
            );
            CREATE TABLE strategy_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_at REAL NOT NULL, strategy TEXT NOT NULL,
                params TEXT, tickers TEXT, results TEXT, notes TEXT
            );
        """)

    # 插入一条旧格式数据
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO jobs (job_id, run_at, date, tickers, n_tickers, "
            "n_success, elapsed, notes) VALUES (?,?,?,?,?,?,?,?)",
            ("old-job-001", 1000.0, "2026-01-01",
             '["sh.600519"]', 1, 1, 5.0, "旧数据"),
        )
        conn.commit()

    # 新建 ResearchDatabase 应能自动迁移
    research = ResearchDatabase(db_path=db)
    job = research.get_job("old-job-001")
    assert job is not None
    assert job["date"] == "2026-01-01"
    # 旧数据的版本字段为 None（正常）
    assert job["run_id"] is None
    assert job["config_hash"] is None

    # 新创建的作业应有完整版本
    reset_run_id_counter()
    new_job_id = research.create_job("2026-08-11", ["sh.600519"],
                                      settings=_MockSettings())
    new_job = research.get_job(new_job_id)
    assert new_job["run_id"] is not None
    assert new_job["config_hash"] is not None


# ── Committee Deliberations ──────────────────────────────────────────────────

def test_insert_committee_deliberation(research_db):
    job_id = research_db.create_job("2026-08-11", ["sh.600519"])
    research_db.insert_committee_deliberation(
        job_id=job_id, ticker="sh.600519", date="2026-08-11",
        bull_case="业绩超预期", bear_case="估值偏高",
        recommendation="BUY", recommendation_confidence=75.0,
        reasoning="综合判断", agent_consensus={"fundamental": "BUY"},
    )
    result = research_db.get_committee_for_ticker("sh.600519")
    assert result is not None
    assert result["ticker"] == "sh.600519"
    assert result["recommendation"] == "BUY"
    assert result["bull_case"] == "业绩超预期"
    assert result["agent_consensus"] == {"fundamental": "BUY"}


def test_get_committee_for_ticker_miss(research_db):
    result = research_db.get_committee_for_ticker("sh.600519")
    assert result is None


# ── Stats Edge Cases ────────────────────────────────────────────────────────

def test_stats_all_tables_empty(research_db):
    stats = research_db.stats()
    assert stats["research_jobs"] == 0
    assert stats["research_ta_analysis"] == 0
    # committee 表可能不存在（迁移前），stats 应返回 0
    assert "research_committee_deliberations" in stats


# ── Signal History ───────────────────────────────────────────────────────────

def test_get_latest_signal_for_ticker(research_db):
    j1 = research_db.create_job("2026-08-10", ["sh.600519"])
    j2 = research_db.create_job("2026-08-11", ["sh.600519"])
    # signal_history 由 signal_lifecycle 写入，测试直接插入
    with research_db._conn as conn:
        conn.execute(
            "INSERT INTO signal_history "
            "(ticker, date, signal, confidence, composite_score, "
            " lifecycle_state, previous_state, transition_reason, job_id, run_id, thesis_snapshot, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("sh.600519", "2026-08-10", "HOLD", 55.0, 70.0,
             "HOLD", None, "initial", j1, None, "", 0.0),
        )
        conn.execute(
            "INSERT INTO signal_history "
            "(ticker, date, signal, confidence, composite_score, "
            " lifecycle_state, previous_state, transition_reason, job_id, run_id, thesis_snapshot, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            ("sh.600519", "2026-08-11", "BUY", 80.0, 80.0,
             "BUY", "HOLD", "improvement", j2, None, "", 0.0),
        )
        conn.commit()
    latest = research_db.get_latest_signal_for_ticker("sh.600519")
    assert latest is not None
    assert latest["signal"] == "BUY"
    assert latest["composite_score"] == 80.0


def test_get_latest_signal_for_ticker_miss(research_db):
    result = research_db.get_latest_signal_for_ticker("sh.600519")
    assert result is None
