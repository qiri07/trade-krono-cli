"""
研究数据库 schema — 表结构定义、常量、SQL 验证。

本模块不含任何运行时数据库逻辑，仅供 migrations.py 和 base.py 引用。
"""

from __future__ import annotations

# Truncation length for thesis stored in research db
REASONING_TRUNCATE_LEN = 500

# Whitelist of allowed research table names — prevents SQL injection via f-strings
RESEARCH_TABLES: frozenset[str] = frozenset(
    {
        "jobs",
        "ta_analysis",
        "kronos_forecast",
        "signals",
        "decisions",
        "raw_reports",
        "backtest_results",
        "strategy_runs",
        "evaluation_results",
        "signal_history",
        "committee_deliberations",
        "data_snapshots",
        "walkforward_runs",
        "experiments",
    }
)


def validate_table_name(table: str, allowed: frozenset[str] | None = None) -> str:
    """Validate a table name against an allowed set. Raises ValueError if invalid."""
    if allowed is None:
        allowed = RESEARCH_TABLES
    if table not in allowed:
        raise ValueError(f"Unauthorized table: {table}")
    return table


# ── CREATE TABLE SQL ──────────────────────────────────────────────────────────
CREATE_SCRIPT = """
    -- 每次运行作业（含版本快照）
    CREATE TABLE IF NOT EXISTS jobs (
                    job_id         TEXT PRIMARY KEY,
                    run_id         TEXT,
                    run_at         REAL NOT NULL,
                    date           TEXT NOT NULL,
                    tickers        TEXT NOT NULL,
                    n_tickers      INTEGER NOT NULL,
                    n_success      INTEGER NOT NULL,
                    elapsed        REAL NOT NULL,
                    data_version   TEXT,
                    model_versions TEXT,
                    prompt_version TEXT,
                    strategy_version TEXT,
                    config_hash    TEXT,
                    external_repos TEXT,
                    notes          TEXT
                );

                -- TA 分析结构化摘要
                CREATE TABLE IF NOT EXISTS ta_analysis (
                    job_id         TEXT NOT NULL,
                    ticker         TEXT NOT NULL,
                    signal         TEXT,
                    confidence     REAL,
                    thesis         TEXT,
                    risks          TEXT,
                    error          TEXT,
                    elapsed        REAL,
                    PRIMARY KEY (job_id, ticker),
                    FOREIGN KEY (job_id) REFERENCES jobs(job_id)
                );

                -- Kronos 预测结构化摘要
                CREATE TABLE IF NOT EXISTS kronos_forecast (
                    job_id          TEXT NOT NULL,
                    ticker          TEXT NOT NULL,
                    direction       TEXT,
                    expected_change REAL,
                    predicted_close REAL,
                    confidence_band TEXT,
                    uncertainty     TEXT,
                    error           TEXT,
                    elapsed         REAL,
                    PRIMARY KEY (job_id, ticker),
                    FOREIGN KEY (job_id) REFERENCES jobs(job_id)
                );

                -- 合并后的综合信号
                CREATE TABLE IF NOT EXISTS signals (
                    job_id                TEXT NOT NULL,
                    ticker                TEXT NOT NULL,
                    rank                  INTEGER,
                    composite_score       REAL,
                    ta_signal             TEXT,
                    ta_confidence         REAL,
                    ta_reasoning          TEXT,
                    kronos_direction      TEXT,
                    kronos_change         REAL,
                    uncertainty           TEXT,
                    ta_error              TEXT,
                    kronos_error          TEXT,
                    signal_assessment_json TEXT,
                    expected_value        REAL,
                    conflict              TEXT,
                    ranking_score         REAL,
                    PRIMARY KEY (job_id, ticker),
                    FOREIGN KEY (job_id) REFERENCES jobs(job_id)
                );

                -- 结构化 InvestmentDecision
                CREATE TABLE IF NOT EXISTS decisions (
                    job_id        TEXT NOT NULL,
                    ticker        TEXT NOT NULL,
                    decision_json TEXT NOT NULL,
                    thesis        TEXT,
                    risks         TEXT,
                    PRIMARY KEY (job_id, ticker),
                    FOREIGN KEY (job_id) REFERENCES jobs(job_id)
                );

                -- 原始报告文件索引
                CREATE TABLE IF NOT EXISTS raw_reports (
                    job_id   TEXT NOT NULL,
                    ticker   TEXT NOT NULL,
                    path     TEXT NOT NULL,
                    reports  TEXT,
                    PRIMARY KEY (job_id, ticker),
                    FOREIGN KEY (job_id) REFERENCES jobs(job_id)
                );

                -- 预留表
                CREATE TABLE IF NOT EXISTS backtest_results (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id         TEXT NOT NULL,
                    strategy       TEXT NOT NULL,
                    symbols        TEXT,
                    start_date     TEXT,
                    end_date       TEXT,
                    results        TEXT,
                    created_at     REAL NOT NULL,
                    scoring_strategy TEXT
                );

                CREATE TABLE IF NOT EXISTS strategy_runs (
                    id             INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_at         REAL NOT NULL,
                    strategy       TEXT NOT NULL,
                    params         TEXT,
                    tickers        TEXT,
                    results        TEXT,
                    notes          TEXT,
                    config_hash    TEXT
                );

                -- 信号生命周期表（同一 ticker 跨多次运行的状态演变）
                CREATE TABLE IF NOT EXISTS signal_history (
                    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                    ticker              TEXT NOT NULL,
                    date                TEXT NOT NULL,
                    signal              TEXT NOT NULL,
                    confidence          REAL NOT NULL,
                    composite_score     REAL NOT NULL,
                    lifecycle_state     TEXT NOT NULL,
                    previous_state      TEXT,
                    transition_reason   TEXT,
                    job_id              TEXT,
                    run_id              TEXT,
                    thesis_snapshot     TEXT,
                    created_at          REAL NOT NULL,
                    FOREIGN KEY (job_id) REFERENCES jobs(job_id)
                );

                -- 委员会审议记录（Investment Committee deliberation results）
                CREATE TABLE IF NOT EXISTS committee_deliberations (
                    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
                    job_id                    TEXT NOT NULL,
                    ticker                    TEXT NOT NULL,
                    date                      TEXT NOT NULL,
                    bull_case                 TEXT,
                    bear_case                 TEXT,
                    recommendation            TEXT,
                    recommendation_confidence REAL,
                    reasoning                 TEXT,
                    agent_consensus           TEXT,
                    created_at                REAL NOT NULL,
                    FOREIGN KEY (job_id) REFERENCES jobs(job_id),
                    UNIQUE (job_id, ticker)
                );

                -- Point-in-Time 数据快照
                CREATE TABLE IF NOT EXISTS data_snapshots (
                    snapshot_id   TEXT PRIMARY KEY,
                    cut_date      TEXT NOT NULL,
                    effective_cut TEXT NOT NULL,
                    sources       TEXT NOT NULL,
                    description   TEXT,
                    created_at    REAL NOT NULL
                );

                -- Walk-Forward 评估结果
                CREATE TABLE IF NOT EXISTS walkforward_runs (
                    run_id         TEXT PRIMARY KEY,
                    experiment_id  TEXT,
                    ticker         TEXT NOT NULL,
                    config_json    TEXT NOT NULL,
                    total_windows  INTEGER,
                    valid_windows  INTEGER,
                    win_rate       REAL,
                    avg_return     REAL,
                    sharpe_annual  REAL,
                    n_records      INTEGER,
                    elapsed_sec    REAL,
                    snapshot_id    TEXT,
                    created_at     REAL NOT NULL,
                    FOREIGN KEY (experiment_id) REFERENCES experiments(experiment_id),
                    FOREIGN KEY (snapshot_id)   REFERENCES data_snapshots(snapshot_id)
                );

                -- 实验注册表
                CREATE TABLE IF NOT EXISTS experiments (
                    experiment_id   TEXT PRIMARY KEY,
                    full_id         TEXT NOT NULL,
                    experiment_type TEXT NOT NULL,
                    hypothesis_json TEXT NOT NULL,
                    description     TEXT,
                    config_json     TEXT,
                    data_snapshot_id TEXT,
                    run_ids         TEXT,
                    result_summary  TEXT,
                    passed          INTEGER,
                    notes           TEXT,
                    created_at      REAL NOT NULL
                );

                -- 性能索引：加速按 job_id 和 ticker 的查询
                CREATE INDEX IF NOT EXISTS idx_ta_analysis_job_id ON ta_analysis(job_id);
                CREATE INDEX IF NOT EXISTS idx_ta_analysis_ticker ON ta_analysis(ticker);
                CREATE INDEX IF NOT EXISTS idx_kronos_forecast_job_id ON kronos_forecast(job_id);
                CREATE INDEX IF NOT EXISTS idx_kronos_forecast_ticker ON kronos_forecast(ticker);
                CREATE INDEX IF NOT EXISTS idx_signals_job_id ON signals(job_id);
                CREATE INDEX IF NOT EXISTS idx_signals_ticker ON signals(ticker);
                CREATE INDEX IF NOT EXISTS idx_decisions_job_id ON decisions(job_id);
                CREATE INDEX IF NOT EXISTS idx_decisions_ticker ON decisions(ticker);
                CREATE INDEX IF NOT EXISTS idx_raw_reports_job_id ON raw_reports(job_id);
                CREATE INDEX IF NOT EXISTS idx_raw_reports_ticker ON raw_reports(ticker);
                CREATE INDEX IF NOT EXISTS idx_signal_history_ticker ON signal_history(ticker);
                CREATE INDEX IF NOT EXISTS idx_signal_history_date ON signal_history(date);
                CREATE INDEX IF NOT EXISTS idx_committee_ticker ON committee_deliberations(ticker);
                CREATE INDEX IF NOT EXISTS idx_committee_job_id ON committee_deliberations(job_id);
                CREATE INDEX IF NOT EXISTS idx_backtest_results_job_id ON backtest_results(job_id);
                CREATE INDEX IF NOT EXISTS idx_strategy_runs_strategy ON strategy_runs(strategy);
                CREATE INDEX IF NOT EXISTS idx_walkforward_ticker ON walkforward_runs(ticker);
                CREATE INDEX IF NOT EXISTS idx_experiments_created ON experiments(created_at);
"""
