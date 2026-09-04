# Changelog

All notable changes to this project will be documented in this file.

The changelog is also available in Chinese: [中文版更新日志](./CHANGELOG_CN.md).

---

### v0.1.8 — 2026-09-04

**Bug fixes, refactoring & test expansion:**

- **fix(cache)**: Fixed SQLite connection leak in `cache.py` — every DB operation now creates a short-lived connection and closes it automatically via new `_transaction()` / `_query_one()` / `_query_all()` helpers, preventing fd exhaustion during long-running pipelines
- **refactor(feishu)**: Eliminated duplicate code in scripts layer — extracted 7 shared functions into `scripts/feishu_utils.py`, removed dead code (orphan function body without def header) from `feishu_core.py`, removed duplicate function bodies from `feishu_notify.py`; net reduction of 260 lines
- **refactor(external)**: Split monolithic `trade_krono_cli/external.py` (557 lines) into a package `trade_krono_cli/external/` with职责分离 modules: `models.py` (data classes), `git_ops.py` (git operations), `config_io.py` (YAML/lock file I/O); `__init__.py` re-exports all public API for backward-compatible imports
- **refactor(data)**: Migrated `next_business_days()` / `validate_data_freshness()` / `safe_float()` from `trade_krono_cli.data` to `trade_krono_cli.utils.helpers`; data.py re-exports for backward compatibility
- **fix(pipeline)**: Fixed frozen dataclass in-place mutation bug in `pipeline_core.py` (TAAnalysis is frozen — must create new instance instead of modifying fields); added `html.escape()` XSS protection in `reporter.py`; replaced `ret != ret` NaN check with `math.isnan()` in `merge.py`; fixed interpolation boundary logic in `risk/liquidity.py`
- **test**: Added 246 new tests across 8 files (test_merge_edge_cases, test_orchestrator, test_akshare_provider, test_tushare_provider, test_version, test_backtest_engine, test_analytics_db, buffett_enhanced_screen); total test count: **2310 passed**

---

### v0.1.7 — 2026-08-27

**GitHub Actions CI/CD & test coverage expansion:**

- **Added `.github/workflows/daily-run.yml`**: Daily research pipeline with schedule trigger (UTC 07:30 = 15:30 Beijing time, weekdays) and manual dispatch with 9 configurable inputs (tickers, date, confidence, signals, auto-universe, skip-kronos, etc.)
- **Added `.github/workflows/ci.yml`**: CI matrix with lint / type-check / test jobs, pytest coverage upload to Codecov
- **Added `scripts/_gh_summary.py`**: GitHub Actions result summary script for workflow output
- **Expanded test coverage** from 1411 → **2065 tests** (+654 new):
  - `test_config_output.py` — `OutputConfig` defaults, merge, roundtrip (6 tests)
  - `test_config_abnormality.py` — `AbnormalityConfig` defaults, validate, merge edge cases (14 tests)
  - `test_config_filters.py` — `FilterConfig` validate/merge with all field combinations (22 tests)
  - `test_prediction_distribution.py` — `compute_single_sample`, `compute_multi_sample`, `build_distribution`, `build_result_dict`, percentile logic (25 tests)
  - `test_data_snapshot.py` — `DataSourceSnapshot`, `DataSnapshot`, `filter_kline_to_cut_date` including frozen dataclass, future-data detection, copy semantics (20 tests)
- Updated test counts in both README.md and README_CN.md architecture diagrams

---

### v0.1.6 — 2026-08-14

**Bug fixes and concurrency hardening:**

- **Fixed `KronosSession` constructor error**: `KronosRunner` doesn't accept `model_name`/`device` kwargs; now passes `session=self` (`models/kronos_session.py`)
- **Fixed `ta_runner.py` variable scope bug**: `t0` referenced in `finally` block was defined in outer method; lifted into `_analyze_one_impl`
- **Fixed missing `trade_date`**: `build_config` return value doesn't include `trade_date`; now explicitly injected before adapter call
- **Fixed missing dynamic attributes on `HorizonMetrics`**: Added default values (0.0) for `rank_ic_*`, `ic_*`, `sortino_ratio`, `calmar_ratio`, `turnover`, `alpha_vs_benchmark` fields
- **Fixed missing IC aggregation fields on `EvaluationSummary`**: Added `ic_composite_rank_mean`, `ic_kronos_rank_mean`, `alpha_best_benchmark` etc.; `_compute_summary` now calls `compute_ic_metrics`
- **Fixed `FailureStore` thread race condition**: Added `threading.Lock` to protect `record()` and `_save()` operations
- **Extended Session cache keys**: `KronosSession` now includes `T`/`top_p`/`lookback`; `TASession` now includes `max_debate_rounds`/`output_language`
- **Fixed SELL signal `expected_return` sign inversion**: Removed incorrect `-pct` negation
- **Fixed `run` command `--config` parameter conflict**: Stock list file parameter renamed to `--stock-file`/`-f`; pipeline config retains `--config`/`-c`
- **Fixed missing `ta_cache_fallback` on `kronos` command**: Added CLI parameter parity with `run` and `ta`
- **SQLite connection timeout**: Added `timeout=10.0` to `cache.py` `connect()` call
- **Test count: 1164 passed** (up from 1106), 0 failures

---

### v0.1.5 — 2026-08-14

**Domain layer & code quality:**

- **New `domain/` layer**: `SignalAssessment`, `InvestmentDecision`, `Experiment`, `Evaluation`, `PredictionDistribution`, `RiskAssessment` as pure domain objects (no I/O, no SQLite coupling)
- **Eliminated duplicate code**: Removed duplicated `SignalConflict` enum, `_compute_ev` function, and `ExperimentType`/`Hypothesis` classes — all now imported from `domain/`
- **Cleaned up `kronos_runner.py`**: Removed duplicate `prediction_distribution` field; unified uncertainty path through `prediction_uncertainty` module only
- **Removed orphan methods from `research_db.py`**: Deleted un-called `insert_signal_assessment`, `insert_decision_domain`, `insert_experiment_domain` (~103 lines removed)
- **New pipeline resource management**: `resource_manager.py` and `resource_pool.py` for LLM client and GPU session lifecycle
- **New `signal_lifecycle.py`**: Track signal state transitions across runs
- **New `experiment_registry.py`**: Hypothesis-driven experiment tracking with pass/fail evaluation
- **New `committee.py`**: LLM-powered Investment Committee deliberation stub
- **New analytics & evaluation**: `analytics_db.py`, `artifact_manifest.py`, `eval_ic.py`, `eval_benchmark.py`, `eval_walkforward.py`
- **Test count**: **1106 passed** (up from 468)

---

### v0.1.4 — 2026-08-13

**Graceful degradation mechanism:**

- Three degradation modes: `strict` (default), `ta_only_on_kronos_fail`, `ta_cache_fallback`
- When Kronos is unavailable, TA-only results are preserved in `ta_only_on_kronos_fail` mode
- When TA analysis fails, the pipeline can fall back to the most recent cached TA result from the research database (`ta_cache_fallback` mode)
- Degraded stocks are visually marked in JSON reports (`degradation_mode` field), HTML badges (`⚠ TA-only` / `📦 缓存TA`), and console tables ("降级模式" column)
- New CLI flags: `--degrade-mode` and `--ta-cache-fallback`
- New env vars: `DEGRADE_MODE`, `TA_CACHE_FALLBACK_ENABLED`, `TA_CACHE_MAX_AGE_DAYS`
- Config validator emits a semantic warning when `TA_CACHE_FALLBACK_ENABLED=true` but `DEGRADE_MODE` is not `ta_cache_fallback`
- Extracted `_apply_ta_cache_fallback()` helper in orchestrator, `_degradation_badge()` in reporter, `_build_degrade_overrides()` in cli_commands
- Test count: **468** (up from 459); added `tests/test_degradation.py` with 33 tests

---

### v0.1.3 — 2026-08-13

**Centralized config validation & tiered management:**

- New `configs/schema.py` — all scoring weights, risk dimension weights, and segment-mapping thresholds (volatility 0%→0 / 60%→100, drawdown breakpoints, liquidity log thresholds, market regime momentum thresholds) are now defined in one place as frozen dataclasses with `validate()` and `merge()` methods
- `RiskEngine` now accepts `RiskConfig`; all `calc_*_risk()` functions accept optional `thresholds` params
- `default_scorer()` / `merge_results()` accept optional `ScoringConfig` / `RiskConfig` params
- `PipelineConfig` carries `scoring: ScoringConfig` and `risk: RiskConfig` fields; `from_dict()` correctly reconstructs nested dataclasses
- `cli_commands._load_env()` calls `run_validation()` at startup — fatal errors exit immediately, warnings are logged
- `config_validator.validate_settings()` now returns `(errors, warnings)` tuple
- **Priority documented**: CLI params > env vars / .env > `PipelineConfig` YAML > schema defaults
- YAML serialization fixed: uses `yaml.safe_dump` (no `!!python/tuple` tags); `to_dict()` recursively converts tuples to lists
- Test count: **468** (up from 459); added `test_custom_thresholds` for all risk modules, `test_from_dict_restores_dataclasses`, `test_merge_works_with_loaded_config`

---

### v0.1.2 — 2026-08-12

**Pipeline consolidation:**

- Deleted redundant root-level `merge.py`, `report.py`, `pipeline.py`, and `pipeline/scorer.py`
- Consolidated merge/scoring/reporting logic into `pipeline/merge.py` and `pipeline/reporter.py`
- `pipeline/__init__.py` now exports only `QuantPipeline` and `PipelineFactory`; submodule internals imported directly
- Removed dead `MergedItem` dataclass, `score_merged_results` (never called in production), and 4 unused imports from `orchestrator.py`
- `filter_pool` now returns `list[StockAnalysisResult]` directly instead of leaking dict wrappers
- Test count: **459** (up from 398); coverage remains at **87%**

---

### v0.1.1 — 2026-08-12

**Code quality & static analysis:**

- **mypy clean**: 0 errors across 38 source files; fixed type annotations in `kronos_runner.py`, `ta_runner.py`, `batch_runner.py`, `cache.py`, `errors.py`, `external.py`, `trading_constraints.py`, `prediction_eval.py`, `pipeline/orchestrator.py`, `logging_config.py`
- **Coverage 87%** (up from 80%): `prediction_eval.py` 94%, `ta_runner.py` 91%, `kronos_runner.py` 83%, `cli.py` 56%
- Test count: **459** (up from 398); added tests for CLI entry points, kronos_runner device resolution, ta_runner config/validation/graph, prediction_eval edge cases, batch runner, config_validator, health checks, merge boundary/uncertainty cases, pipeline consolidation

**New packages (modular refactor):**

- `pipeline/` — orchestrator (ThreadPoolExecutor + raw report auto-save), data_fetcher (parallel K-line fetch), merge (scoring + risk penalty + ranking), reporter (JSON/HTML/console)
- `models/` — kronos_session (lazy-load, device selection), ta_session (provider/debate state)
- `batch/` — batch_runner (async semaphore-based batch predictions)

**Tests added:** `test_cli.py`, `test_kronos_runner.py`, `test_ta_runner.py`, `test_prediction_eval.py`, `test_batch_runner.py`, `test_config_validator.py`, `test_health.py`, `test_merge_edge_cases.py`, `test_merge_uncertainty.py`, `integration/test_pipeline_integration.py`

---

### v0.1.0 — 2026-08-12

**Code quality & security fixes:**

- Added `sanitize_for_log()` to `security.py` — single shared helper for API-key redaction; replaces duplicated inline regex in `kronos_runner.py` and `ta_runner.py`
- Added `ensure_import_path()` to `security.py` — deduplicates the harness-first sys.path injection from both runner modules
- Fixed `_TRAIDINGAGENTS_IMPORTED` typo → `_TRADINGAGENTS_IMPORTED` in `ta_runner.py`
- Extracted magic truncation literals (`[:500]`, `[:300]`) to module-level constants across `ta_runner.py`, `pipeline/merge.py`, `ta_decision.py`, `cache.py`
- Restructured `EvaluationSummary` — replaced 30+ flat fields with a `HorizonMetrics` dataclass grouped by horizon; updated all consumers
- Fixed SQL f-string table-name interpolation in `cache.py` — added `_validate_table_name()` whitelist helper
- Removed duplicate `@dataclass` decorator on `HorizonMetrics`

**Tests:** 4 new in `test_security.py`, 2 new in `test_prediction_eval.py` — 162 total passing.
