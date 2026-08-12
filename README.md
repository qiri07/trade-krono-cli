# trade-krono-cli

> A-Share Research + Kronos Prediction Integrated Pipeline — Parallel Analysis of N Stocks

[![Python](https://img.shields.io/badge/Python-3.12-blue.svg)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

## Overview

`trade-krono-cli` is a CLI tool that accepts N A-share stock ticker symbols and **synchronously parallel-calls**:

1. **TradingAgents-astock** — [https://github.com/simonlin1212/TradingAgents-astock](https://github.com/simonlin1212/TradingAgents-astock) multi-Agent deep analysis (market/sentiment/fundamentals/policy/capital/risk debate)
2. **Kronos** — K-line sequence prediction [https://github.com/shiyu-coder/Kronos](https://github.com/shiyu-coder/Kronos) (deep learning-based future price trend prediction with uncertainty quantification)

Results are automatically merged after both complete, producing a ranked report.

## Table of Contents

- [Quick Start](#quick-start)
- [Installation](#installation)
- [Configuration](#configuration)
- [Usage Guide](#usage-guide)
- [Output Format](#output-format)
- [Architecture](#architecture)
- [Prediction Uncertainty Quantification](#prediction-uncertainty-quantification)
- [Scoring Formula](#scoring-formula)
- [Risk Engine](#risk-engine)
- [External Repo Manager](#external-repo-manager)
- [Testing](#testing)
- [TA Decision Extraction Logic](#ta-decision-extraction-logic)
- [Three-Tier Raw Report Storage](#three-tier-raw-report-storage)
- [InvestmentDecision Standardization](#investmentdecision-standardization)
- [Security Notes](#security-notes)
- [Notes](#notes)

---

## Quick Start

```bash
# Create virtual environment (Python 3.12 required)
uv venv .venv --python 3.12
source .venv/bin/activate
uv pip install -e ".[dev]"

# Configure (copy and edit .env)
cp .env.example .env

# Clone external dependencies (or create symlinks to existing copies)
# ln -s /your/path/TradingAgents-astock external/TradingAgents-astock
# ln -s /your/path/Kronos external/Kronos

# Create external project config file
cat > external/repos.yaml << 'EOF'
repos:
  tradingagents:
    path: external/TradingAgents-astock
    branch: main
    url: https://github.com/simonlin1212/TradingAgents-astock
    commit: null
  kronos:
    path: external/Kronos
    branch: main
    url: https://github.com/shiyu-coder/Kronos
    commit: null
EOF

# One-command run (TA + Kronos parallel)
.venv/bin/python -m trade_krono_cli.cli run --tickers "600519,000858,600036" --date 2026-08-11
```

## Installation

### Method 1: uv Virtual Environment (Recommended)

```bash
cd trade-krono-cli
uv venv .venv --python 3.12
source .venv/bin/activate
uv pip install -e ".[dev]"
```

> **Note**: This project requires **Python 3.12**. Python 3.14 is not yet supported (no torch wheel for cp314, and PEP 668 blocks system-wide pip installs).

### Method 2: pip Install

```bash
cd trade-krono-cli
pip install -e .
```

For development dependencies (tests, etc.):

```bash
pip install -e ".[dev]"
```

### Method 3: One-Click Install Script

```bash
bash scripts/install.sh
```

The install script will:
- Check Python version (>= 3.12)
- Install dependencies
- Create `outputs/` directory
- Generate `.env.example` template if `.env` is missing

### Environment Requirements

| Dependency | Minimum Version | Notes |
|------|----------|------|
| Python | 3.12 | Python 3.14 not yet supported (no torch wheel) |
| PyTorch | 2.13+ (cu130) | Kronos model inference; install via `.venv/bin/uv pip install torch` |
| Typer | 0.9+ | CLI framework |
| Rich | 13+ | Terminal beautification output |
| Python-dotenv | 1.0+ | .env file loading |

## Configuration

### `.env` File

Create a `.env` file in the project root. All settings can be overridden via environment variables — no code changes needed.

```bash
# ── LLM API Key (at least one required) ────────────────────────
DEEPSEEK_API_KEY=sk-xxx
# OPENAI_API_KEY=sk-xxx
# ANTHROPIC_API_KEY=sk-ant-xxx
# MINIMAX_API_KEY=xxx
# AGNES_API_KEY=xxx

# ── LLM Behavior Config ────────────────────────────────────────
LLM_PROVIDER=deepseek          # Default LLM provider
DEEP_THINK_LLM=deepseek-chat   # Deep thinking model
QUICK_THINK_LLM=deepseek-chat  # Quick thinking model
BACKEND_URL=https://apihub.agnes-ai.cn/v1  # Backend API URL (optional)
MAX_DEBATE_ROUNDS=1            # Max debate rounds
MAX_RISK_DISCUSS_ROUNDS=1      # Max risk discussion rounds
CHECKPOINT_ENABLED=true        # Enable checkpoint (skip completed analysis)
OUTPUT_LANGUAGE=Chinese        # Report output language

# ── Kronos Config ──────────────────────────────────────────────
KRONOS_MODEL=kronos-base       # Model name
KRONOS_TOKENIZER=kronos-Tokenizer-base  # Tokenizer name
KRONOS_DEVICE=cpu              # cpu / cuda:0 (requires GPU)
KRONOS_LOOKBACK=400            # Historical K-line lookback length
KRONOS_PRED_LEN=30             # Prediction steps
KRONOS_SAMPLE_COUNT=1          # Sample count (>1 enables real uncertainty quantification)
KRONOS_T=1.0                   # Sampling temperature
KRONOS_TOP_P=0.9               # Nucleus sampling threshold
KRONOS_USE_SAMPLE_CONFIDENCE=false  # Enable sample-based uncertainty quantification

# ── Filter Config ──────────────────────────────────────────────
MIN_CONFIDENCE=55.0            # Minimum TA confidence threshold
ALLOWED_SIGNALS=BUY,HOLD       # Allowed TA signals (comma-separated)

# ── Data Fetch Config ──────────────────────────────────────────
BAOSTOCK_SLEEP_SEC=1.0         # baostock request interval (seconds)

# ── Path Config (defaults usually don't need changes) ─────────
# TRADINGAGENTS_ROOT=/path/to/TradingAgents-astock
# KRONOS_ROOT=/path/to/Kronos
```

### Configuration Details

#### LLM Configuration

| Variable | Default | Description |
|------|--------|------|
| `LLM_PROVIDER` | `deepseek` | Default provider: deepseek / openai / anthropic / minimax / agnes |
| `DEEP_THINK_LLM` | `deepseek-chat` | Model used by deep analysis Agent |
| `QUICK_THINK_LLM` | `deepseek-chat` | Model used by quick analysis Agent |
| `BACKEND_URL` | — | LLM backend API URL, required by some providers |
| `MAX_DEBATE_ROUNDS` | `1` | Max多空 debate rounds, 0 = no debate |
| `MAX_RISK_DISCUSS_ROUNDS` | `1` | Max risk discussion rounds |
| `CHECKPOINT_ENABLED` | `true` | Skip cached TA analysis results when enabled |
| `OUTPUT_LANGUAGE` | `Chinese` | Report language: Chinese / English |

#### Kronos Configuration

| Variable | Default | Description |
|------|--------|------|
| `KRONOS_MODEL` | `kronos-base` | Model name, must match local path |
| `KRONOS_TOKENIZER` | `kronos-Tokenizer-base` | Tokenizer name |
| `KRONOS_DEVICE` | `cpu` | Inference device, `cpu` or `cuda:0` |
| `KRONOS_LOOKBACK` | `400` | Number of historical K-lines for prediction |
| `KRONOS_PRED_LEN` | `30` | How many K-lines to predict into the future |
| `KRONOS_SAMPLE_COUNT` | `1` | Sample count; `>1` takes cross-sample mean and computes real uncertainty |
| `KRONOS_T` | `1.0` | Sampling temperature, higher = more random |
| `KRONOS_TOP_P` | `0.9` | Nucleus sampling threshold |
| `KRONOS_USE_SAMPLE_CONFIDENCE` | `false` | Enable sample-based real uncertainty quantification |

#### Filter Configuration

| Variable | Default | Description |
|------|--------|------|
| `MIN_CONFIDENCE` | `55.0` | Stocks below this TA confidence are excluded from ranking |
| `ALLOWED_SIGNALS` | `BUY,HOLD` | Only keep stocks with signals in this list |

#### Path Configuration

| Variable | Default | Description |
|------|--------|------|
| `TRADINGAGENTS_ROOT` | — | TradingAgents-astock project root (can be replaced by `external/repos.yaml`) |
| `KRONOS_ROOT` | — | Kronos project root (can be replaced by `external/repos.yaml`) |

> **Tip**: We recommend using `external/repos.yaml` to manage external project paths (see [External Repo Manager](#external-repo-manager)). Environment variables serve as fallback.

### Override via Environment Variables

`.env` settings can also be overridden via CLI environment variables:

```bash
export LLM_PROVIDER=openai
export KRONOS_DEVICE=cuda:0
.venv/bin/python -m trade_krono_cli.cli run --tickers "600519" --date 2026-08-11
```

## Usage Guide

### Command Overview

```
trade-krono-cli run        # One-command run: TA + Kronos parallel pipeline
trade-krono-cli ta         # TradingAgents stock selection analysis only
trade-krono-cli kronos     # Kronos batch prediction only
trade-krono-cli status     # View system status (keys, cache, models)
trade-krono-cli clear-cache # Clear all caches
```

### `run` — Full Pipeline

```bash
# Basic usage
.venv/bin/python -m trade_krono_cli.cli run --tickers "600519,000858,600036" --date 2026-08-11

# Run TA only (skip Kronos)
.venv/bin/python -m trade_krono_cli.cli run --tickers "600519,000858" --date 2026-08-11 --skip-kronos

# Custom confidence threshold and signal filter
.venv/bin/python -m trade_krono_cli.cli run --tickers "600519,000858" --date 2026-08-11 \
  --min-confidence 60 --signals "BUY,HOLD"

# Custom Kronos parameters
.venv/bin/python -m trade_krono_cli.cli run --tickers "600519" --date 2026-08-11 \
  --pred-len 60 --lookback 800

# Use config file (one stock per line, supports # comments)
cat > stocks.txt << 'EOF'
600519
000858
# 600036  # comment line
EOF
.venv/bin/python -m trade_krono_cli.cli run --config stocks.txt --date 2026-08-11

# Disable cache
.venv/bin/python -m trade_krono_cli.cli run --tickers "600519" --date 2026-08-11 --no-cache
```

**`run` parameters:**

| Parameter | Default | Description |
|------|--------|------|
| `--tickers, -t` | — | Comma-separated stock codes (mutually exclusive with --config) |
| `--config, -c` | — | Stock list file path (mutually exclusive with --tickers) |
| `--date, -d` | — | Analysis date YYYY-MM-DD (required) |
| `--min-confidence` | `55.0` | Minimum TA confidence |
| `--signals` | `BUY,HOLD` | Allowed TA signals |
| `--skip-kronos` | `false` | Skip Kronos prediction |
| `--pred-len` | `30` | Kronos prediction steps |
| `--lookback` | `400` | Kronos historical lookback length |
| `--json` | `outputs/results.json` | JSON report output path |
| `--html` | `outputs/report.html` | HTML report output path |
| `--no-cache` | `false` | Disable cache |

### `ta` — TradingAgents Only

```bash
.venv/bin/python -m trade_krono_cli.cli ta --tickers "600519,000858" --date 2026-08-11
.venv/bin/python -m trade_krono_cli.cli ta --tickers "600519" --date 2026-08-11 --output outputs/ta_result.json
```

### `kronos` — Kronos Only

```bash
.venv/bin/python -m trade_krono_cli.cli kronos --tickers "600519" --date 2026-08-11 --pred-len 60 --lookback 800
```

### `status` — System Status

```bash
.venv/bin/python -m trade_krono_cli.cli status
```

Output includes: key status, path configuration, cache statistics.

### `clear-cache` — Clear Cache

```bash
.venv/bin/python -m trade_krono_cli.cli clear-cache
```

Clears all K-line data, TA analysis, and Kronos prediction caches.

## Output Format

### Console Output

```
🚀 Launching pipeline 3 stocks → 2026-08-11
  TA Analysis [0/2]
  TA Analysis [1/2]
  TA Analysis [2/2]
  Parallel Execution [1/2]
  Parallel Execution [2/2]
┌──────────┬────────┬────────┬──────────┬───────────┬────────────┬──────────┬──────────┐
│   Rank   │ Code   │ TA Sig │ Confidence │ Kronos Dir │ Exp.Chg%   │ KronosConf │ Score   │
├──────────┼────────┼────────┼──────────┼───────────┼────────────┼──────────┼──────────┤
│    1     │600519  │  BUY   │   80.0   │    UP     │    3.20    │   72.0   │  82.10   │
│    2     │000858  │  HOLD  │   60.0   │   DOWN    │   -1.50    │   55.0   │  45.00   │
└──────────┴────────┴────────┴──────────┴───────────┴────────────┴──────────┴──────────┘
✅ Done → outputs/results.json
```

### JSON Report

Path: `outputs/results.json` (specified by `--json` parameter)

```json
[
  {
    "rank": 1,
    "ticker": "sh.600519",
    "ta_signal": "BUY",
    "ta_confidence": 80.0,
    "ta_reasoning": "Solid fundamentals...",
    "kronos_direction": "UP",
    "kronos_change_pct": 3.2,
    "kronos_last_close": 1780.5,
    "kronos_pred_close": 1837.73,
    "kronos_prediction_uncertainty": {
      "expected_return": 3.2,
      "direction": "UP",
      "direction_confidence": 0.72,
      "volatility": 12.5,
      "path_dispersion": null,
      "confidence_score": 72.0,
      "sample_count_used": 1
    },
    "composite_score": 82.1
  }
]
```

### HTML Report

Path: `outputs/report.html` (specified by `--html` parameter). Automatically generates a styled HTML table. Hover over the confidence column to see direction_confidence / path_dispersion / confidence_score details.

### Logs

- Pipeline log: `outputs/pipeline.log`
- Memory log: `outputs/memory_log.jsonl` (performance metrics per run)

## Version Tracking

The core question for any quant system: **re-run half a year later, get different results, but don't know if it's the data, model, or config that changed.**

Each analysis job automatically records a complete version snapshot:

```json
{
  "run_id": "20260811-143022-001",
  "data_version": "baostock-2026-08-11",
  "model_versions": {
    "kronos": "kronos-kronos-base-kronos-Tokenizer-base-cpu",
    "llm": "deepseek/deepseek-chat+deepseek-chat"
  },
  "prompt_version": "ta-v1r1-chinese",
  "strategy_version": "0.1.0",
  "config_hash": "a3f8c2d1e5b79046"
}
```

| Field | Meaning | When It Changes |
|------|------|---------|
| `run_id` | Unique run identifier (format: `YYYYMMDD-HHMMSS-NNN`) | Auto-generated each run |
| `data_version` | Data source snapshot version | Data source update or date change |
| `model_versions.kronos` | Kronos model + Tokenizer + device | Model/device change |
| `model_versions.llm` | LLM provider + model | Provider or model switch |
| `prompt_version` | TA prompt parameter version | Debate rounds/language config change |
| `strategy_version` | Project version (= pyproject.toml version) | Version upgrade |
| `config_hash` | Strategy config SHA256 first 16 chars | Any strategy parameter change |

### Reproducibility Examples

```bash
# View historical signal trajectory for a stock (with version info per run)
.venv/bin/python -m trade_krono_cli.cli history -t sh.600519

# View recent analysis jobs (with run_id and data version)
.venv/bin/python -m trade_krono_cli.cli history

# View system status (record counts per table and version summary of recent jobs)
.venv/bin/python -m trade_krono_cli.cli status
```

## Prediction Evaluation

The core of any quant system: verify whether predictions actually have Alpha.

```bash
# Evaluate predictions on historical data
.venv/bin/python -m trade_krono_cli.cli eval-prediction

# Specify date range
.venv/bin/python -m trade_krono_cli.cli eval-prediction --from 2026-01-01 --to 2026-08-11

# Evaluate only specific stocks
.venv/bin/python -m trade_krono_cli.cli eval-prediction -i sh.600519,sz.000858

# View stored evaluation results (without recomputing)
.venv/bin/python -m trade_krono_cli.cli eval-prediction --latest
```

Evaluation Metrics:

| Module | Metric | Description |
|------|------|------|
| **Kronos** | 5D/10D/20D Direction Accuracy | Predicted direction vs actual direction (>50% = beats random) |
| **Kronos** | MAE / RMSE | Mean error of predicted vs actual return % |
| **TA BUY** | Win Rate + Avg Return | Performance of all BUY signals held for N days |
| **TA HOLD** | Avg Return | Benchmark return (control group) |
| **Combined Signal** | TA BUY + Kronos UP Win Rate | Dual-confirmed signal performance |
| **High Confidence** | Win Rate of Composite Score ≥ 70 | Whether high-confidence signals are truly more reliable |

Baseline: Random direction accuracy 50%, win rate 50%.

## Architecture

```
trade-krono-cli
├── trade_krono_cli/
│   ├── cli.py              # Typer CLI entry (run / ta / kronos / status / history)
│   ├── config.py           # Configuration management (.env → Settings singleton)
│   ├── data.py             # K-line data fetching (baostock)
│   ├── security.py         # Key validation + input validation + retry + rate limiting
│   ├── cache.py            # Cache (TTL performance cache) + ResearchDatabase (persistent records)
│   ├── logger.py           # Logging configuration
│   ├── ta_decision.py      # Investment decision standardization (Signal / InvestmentDecision / DecisionAdapter)
│   ├── ta_runner.py        # TradingAgents wrapper (with save_raw_reports three-tier storage)
│   ├── kronos_runner.py    # Kronos prediction wrapper (with prediction_uncertainty module)
│   ├── merge.py            # Result merge + scoring (with risk penalty)
│   ├── report.py           # JSON/HTML/console reports
│   ├── prediction_eval.py  # Prediction evaluation (Kronos/TA/combined signal win rate validation)
│   ├── pipeline.py         # Parallel pipeline orchestration (auto raw report save + research DB write)
│   ├── external.py         # External repo management (repo status/doctor/update/pin)
│   └── risk/               # Risk engine (volatility / drawdown / liquidity / concentration / market regime)
├── scripts/
│   └── install.sh          # One-click install script
├── tests/                  # Test suite (156 tests all passing)
```

### Cache vs Research Database

The system uses the same SQLite file but is conceptually split into two layers:

**Cache (Performance Optimization, TTL-driven)**

| Table | Purpose | TTL |
|----|------|-----|
| `kline_cache` | K-line data (Pickle serialized) | 1h (intraday) / 24h (daily) |
| `ta_cache` | TA analysis results (JSON) | 24h |
| `kronos_cache` | Kronos prediction results (JSON) | 24h |

These tables are used to **accelerate repeat queries** — re-analyzing the same stock on the same date returns cached data directly. Data can be discarded after TTL expires.

**ResearchDatabase (Persistent Storage, No TTL)**

| Table | Purpose |
|----|------|
| `jobs` | Metadata for each analysis job (unique job_id, date, stock list, elapsed time) |
| `ta_analysis` | Structured TA analysis summary (signal, confidence, thesis, risks) |
| `kronos_forecast` | Structured Kronos prediction summary (direction, expected return, uncertainty) |
| `signals` | Merged composite signals (ranking, composite score, dual-source data) |
| `decisions` | Complete `InvestmentDecision` JSON (including thesis + risks) |
| `raw_reports` | Disk path index for raw report files |
| `backtest_results` | Strategy backtest results (reserved) |
| `strategy_runs` | Strategy run records (reserved) |

These tables are used for **historical回溯** — answering "which stocks were analyzed last time?" or "what's the historical signal for a given stock?". Data is never auto-cleaned.

### Parallel Strategy

`pipeline.py` uses `concurrent.futures.ThreadPoolExecutor`:
- TA analysis runs sequentially (shared LLM API, avoids concurrent rate limiting)
- Kronos prediction runs sequentially (avoids GPU memory contention in GPU mode; could be parallelized in CPU mode)
- TA and Kronos run **asynchronously**: both start in parallel, merge after both complete
- Single-stock failures are isolated (one failure doesn't break the batch)
- Each run automatically creates a `jobs` record; results written to `ta_analysis` / `kronos_forecast` / `signals` / `decisions` tables

## Prediction Uncertainty Quantification

### Background

The original `confidence_band` used quartiles (25%/75%) of a single prediction path's time steps.
When `sample_count=1` (default), `q_low == q_high == mean` — **statistically meaningless**.

Refactored to introduce an independent **`prediction_uncertainty`** submodule, replacing the old meaningless interval.

### Field Definitions

| Field | Meaning | Calculation | sample_count=1 |
|------|------|---------|----------------|
| `expected_return` | Expected return (%) | `(final_close - last_close) / last_close * 100` | ✅ Valid |
| `direction` | Direction label | UP / DOWN / FLAT (±1% threshold) | ✅ Valid |
| `direction_confidence` | Direction confidence | `sigmoid(|change_pct| / (10*std + eps))` ∈ [0,1] | ✅ Valid |
| `volatility` | Prediction path volatility | `std(close_values)` | ✅ Valid |
| `path_dispersion` | Path dispersion | `std / \|mean\|` (cross-sample statistic) | `null` (no statistical meaning) |
| `confidence_score` | Composite confidence score | 0–100 (see formula below) | ✅ Valid |
| `sample_count_used` | Actual sample count | — | ✅ Recorded |

### confidence_score Formula

```
# sample_count = 1 (single path, degenerate mode)
confidence_score = direction_confidence * 100

# sample_count > 1 (multi-sample, real uncertainty)
confidence_score = min(100, direction_confidence * 50 + max(0, 50 - path_dispersion * 200))
```

### Enabling Multi-Sample Uncertainty

```bash
# Method 1: Modify .env
KRONOS_SAMPLE_COUNT=5
KRONOS_USE_SAMPLE_CONFIDENCE=true

# Method 2: Environment variable override
export KRONOS_SAMPLE_COUNT=5
.venv/bin/python -m trade_krono_cli.cli run --tickers "600519" --date 2026-08-11
```

> **Note**: With `sample_count > 1`, each stock runs inference multiple times and takes the mean. Inference time increases roughly `sample_count`-fold, but you gain real cross-path uncertainty estimates.

## Scoring Formula

```
score = TA_confidence * 0.4
      + Kronos_change_map * 0.3
      + direction_bonus   * 0.1
      + confidence_score  * 0.1

Where:
- TA_confidence:     0-100, direct mapping
- Kronos_change_map: [-50%, +50%] -> [0, 100] (linear mapping)
- direction_bonus:   UP = +10, FLAT = 0, DOWN = -10
- confidence_score:  from prediction_uncertainty.confidence_score (0-100)
```

The highest composite score ranks first. Compared to the old formula (40%/40%/20%), the new version reduces the weight on price change (40%→30%) and introduces an uncertainty quantification bonus (10%), making rankings more robust.

## Risk Engine

Multi-dimensional risk quantification for each candidate stock, outputting a 0-100 risk score that acts as a penalty factor in the composite score.

### Risk Dimensions

| Dimension | Source | Logic | Weight |
|------|----------|------|------|
| **Volatility Risk** | 20-day annualized std of K-line daily returns | Higher volatility = higher risk (0%→0, 60%→100) | 30% |
| **Drawdown Risk** | 60-day rolling max → max drawdown | Larger drawdown = higher risk (5%→20, 40%→100) | 25% |
| **Liquidity Risk** | 20-day avg volume + market cap | Lower volume = higher risk (分段映射) | 20% |
| **Concentration Risk** | Placeholder (reserved portfolio weight interface) | Default 10 points | 10% |
| **Market Regime Risk** | 20-day + 60-day momentum | Downtrend = high risk, uptrend = low risk | 15% |

### Output Example

```
====================================
  Risk Score for sh.600519 (2026-08-11)
====================================
  Liquidity Risk       8
  Volatility Risk     12
  Drawdown Risk       15
  Concentration Risk    5
  Market Regime Risk  10
------------------------------------
  Total Risk         50.0
====================================
```

### Relationship to Composite Scoring

The risk score enters `default_scorer` as a **penalty factor**:

```
risk_penalty = total_risk / 100 × 15   (max deduction: 15 points)
final_score  = base_score - risk_penalty
```

High-risk stocks (e.g., total risk 80) can be penalized up to 12 points (80% × 15), naturally down-weighting them in the ranking.

### Module Structure

```
trade_krono_cli/risk/
├── volatility.py    # Volatility risk
├── drawdown.py      # Drawdown risk
├── liquidity.py     # Liquidity risk
├── concentration.py # Concentration risk (reserved interface)
├── market_regime.py # Market regime risk
└── risk_engine.py   # Aggregation engine + RiskScore dataclass
```

### Usage

```python
from trade_krono_cli.risk import RiskEngine, assess_risk
import pandas as pd

# Method 1: Convenience function
engine = RiskEngine()
risk = engine.assess(ticker, date, kline_df, quote_data={"market_cap": 200.0})
print(risk.print_report())

# Method 2: Call sub-module directly
from trade_krono_cli.risk.volatility import calc_volatility_risk
score, ann_vol = calc_volatility_risk(close_series)
```

## External Repo Manager

Manage dependent downstream projects (TradingAgents-astock, Kronos) to ensure reproducible results.

### Why?

Directly referencing external paths (`TRADINGAGENTS_ROOT=/path/to/...`) has problems:

| Problem | Description |
|------|------|
| Path dependency | Configuration breaks after moving to a different machine/directory |
| Untunable versions | Don't know which commit was used for historical results |
| Unknown dirty state | Local modifications may cause result drift |
| PyPI coupling | `cli-anything-tradingagents` as a PyPI dependency breaks external project independence |

### File Responsibilities

```
external/
├── repos.yaml    ← Human-editable: paths, branches, URLs (manually maintained)
└── repo.lock     ← Machine-maintained: locked commit SHA + timestamp (auto-written)
```

**`repo.lock` is the source of truth for reproducibility.** Auto-written after each `repo pin` or `repo update`.
Recommended: commit `repo.lock` to git (track changes), while `repos.yaml` can hold just path info.

### Config File Example

```yaml
# external/repos.yaml
repos:
  tradingagents:
    path: external/TradingAgents-astock
    branch: main
    url: https://github.com/simonlin1212/TradingAgents-astock
    commit: null          # null = track branch; non-null = pinned to that commit
  kronos:
    path: external/Kronos
    branch: main
    url: https://github.com/shiyu-coder/Kronos
    commit: null
```

### repo.lock Example

```json
{
  "generated_at": "2026-08-11T21:30:00",
  "repos": {
    "tradingagents": {
      "commit": "abc123def456789...",
      "commit_short": "abc123def456",
      "pinned_at": "2026-08-11T21:30:00",
      "branch": "main",
      "dirty": false
    },
    "kronos": {
      "commit": "def789ghi012345...",
      "commit_short": "def789ghi012",
      "pinned_at": "2026-08-11T21:30:00",
      "branch": "main",
      "dirty": false
    }
  }
}
```

### CLI Commands

```bash
# View all external repo status (branch / commit / locked / dirty / lock drift)
.venv/bin/python -m trade_krono_cli.cli repo status

# Diagnose problems (path missing, dirty, lock drift, branch mismatch)
.venv/bin/python -m trade_krono_cli.cli repo doctor

# Pull latest code, auto-refresh repo.lock (unpinned repos only)
.venv/bin/python -m trade_krono_cli.cli repo update

# Pin to a specific commit (writes to both repos.yaml + repo.lock)
.venv/bin/python -m trade_krono_cli.cli repo pin tradingagents abc123def456
.venv/bin/python -m trade_krono_cli.cli repo pin kronos def789ghi012
```

### Decoupling from PyPI

`trade-krono-cli` **no longer uses `cli-anything-tradingagents` as a PyPI dependency**.

```
Old architecture (coupled):              New architecture (fully decoupled):
trade-krono-cli                           trade-krono-cli
  ├─ pip install cli-anything-             ├─ external/repos.yaml   (path + branch)
       tradingagents                       ├─ external/repo.lock    (commit lock)
  └─ import tradingagents                  └─ sys.path.insert(external/)
```

This means:
- Pinning a specific TradingAgents commit only requires `repo pin`, no PyPI republish needed
- External projects can be freely modified, unconstrained by `pyproject.toml`
- `repo doctor` detects lock drift (code updated but lock not refreshed)

### What Pin Does

When `commit` is non-null:
1. Each run automatically checks whether current HEAD matches the recorded commit in repo.lock
2. If inconsistent, `repo doctor` reports a `lock drift` error
3. The `external_repos` snapshot in historical analysis records the commit used at that time
4. Re-run the same script half a year later and results are fully reproducible

### In Version Snapshots

Each analysis job's `external_repos` field is auto-recorded:

```json
{
  "external_repos": {
    "tradingagents": {
      "commit": "abc123def456",
      "branch": "main",
      "pinned": true,
      "locked": true,
      "dirty": false,
      "lock_mismatch": false
    },
    "kronos": {
      "commit": "def789ghi012",
      "branch": "main",
      "pinned": false,
      "locked": true,
      "dirty": false,
      "lock_mismatch": false
    }
  }
}
```

View external repos info for a stock's historical analyses via `trade-krono-cli history -t sh.600519`.

### Migration Guide

If currently using `TRADINGAGENTS_ROOT` / `KRONOS_ROOT` environment variables:

```bash
# 1. Move external projects under external/ (or create symlinks)
# Replace example paths with your actual locations
ln -s /your/path/TradingAgents-astock external/TradingAgents-astock
ln -s /your/path/Kronos external/Kronos

# 2. Create config file
cat > external/repos.yaml << 'EOF'
repos:
  tradingagents:
    path: external/TradingAgents-astock
    branch: main
    url: https://github.com/simonlin1212/TradingAgents-astock
    commit: null
  kronos:
    path: external/Kronos
    branch: main
    url: https://github.com/shiyu-coder/Kronos
    commit: null
EOF

# 3. Initialize lock file and verify
.venv/bin/python -m trade_krono_cli.cli repo update   # auto-writes repo.lock
.venv/bin/python -m trade_krono_cli.cli repo status
.venv/bin/python -m trade_krono_cli.cli repo doctor
```

## Dependencies

### Python Dependencies (pyproject.toml)

| Package | Purpose |
|----|------|
| `typer` | CLI framework |
| `rich` | Terminal beautification output |
| `loguru` | Logging |
| `python-dotenv` | .env loading |
| `pyyaml` | YAML config file read/write |
| `pandas` + `baostock` | A-share data fetching |
| `torch` | Kronos model inference |
| `pytest` | Test framework (dev dependency) |

### External Projects (read-only calls, source not modified)

| Project | GitHub | Purpose |
|------|--------|------|
| `TradingAgents-astock` | [simonlin1212/TradingAgents-astock](https://github.com/simonlin1212/TradingAgents-astock) | TA multi-Agent deep analysis |
| `Kronos` | [shiyu-coder/Kronos](https://github.com/shiyu-coder/Kronos) | K-line sequence prediction |

Called via `sys.path` injection; original project code is not modified.

## Testing

```bash
pytest tests/ -v
```

Test Results: **156/156 all passing** (including end-to-end pipeline, cache serialization, error isolation scenarios)

| File | Coverage |
|------|----------|
| `test_cli.py` | CLI entry, parameter parsing, stock list loading, eval-prediction command |
| `test_data.py` | K-line data fetching, cache read/write, TTL expiry |
| `test_merge.py` | Result merge logic, scoring formula, filter pool |
| `test_pipeline.py` | Pipeline orchestration, error isolation |
| `test_report.py` | JSON/HTML/console report generation |
| `test_security.py` | Key validation, input validation, retry, rate limiting |
| `test_ta_decision.py` | DecisionAdapter structured parsing, InvestmentDecision dataclass, raw report storage |
| `test_research_db.py` | ResearchDatabase full-table CRUD, jobs lifecycle, schema migration, cache/research isolation |
| `test_version.py` | run_id generation, version snapshot construction, config_hash, backward-compatible migration |
| `test_prediction_eval.py` | EvalRecord, EvaluationSummary, statistical calculation logic |
| `test_risk.py` | Risk engine (volatility/drawdown/liquidity/concentration/market regime) full-dimension tests |
| `test_external.py` | External repo management (config I/O, status, pin, lock drift detection) |

## TA Decision Extraction Logic

The `signal` and `confidence` in TA analysis results are extracted from `final_state` by `_extract_decision()` using a three-tier strategy:

```
Priority 1: **Rating**: <value> structured field
  → Direct signal + base confidence mapping
  → Supports: Strong Buy/Buy/Overweight/Neutral/Hold/Underweight/Sell/Strong Sell

Priority 2: Negative-context-aware keyword matching
  → First check if there are negation words (NOT/NO/FAIL etc.) within 5 words before the target word
  → Avoids misjudging "not recommend BUY" as BUY

Priority 3: fallback
  → signal=HOLD, confidence=50
```

Confidence fine-tuning:
- `position_size` corroboration: larger position ratio → confidence +5 when confirming signal
- `agent_scores` divergence:多空 opinion spread > 20 → confidence -5

| Rating | Signal | Base Confidence |
|--------|--------|-----------|
| Strong Buy | BUY | 95 |
| Buy | BUY | 80 |
| Overweight | BUY | 70 |
| Neutral / Hold | HOLD | 50 |
| Underweight | SELL | 40 |
| Sell | SELL | 30 |
| Strong Sell | SELL | 15 |

## Three-Tier Raw Report Storage

To prevent permanent loss of historical information caused by LLM report truncation, the system uses a three-tier storage architecture:

```
outputs/results/raw/{date}/{ticker}.json    ← raw: complete raw report (never truncated)
outputs/results/results.json                 ← structured: core fields + summary (SQLite cache)
outputs/report.html                          ← summary: 500-char display layer
```

| Tier | Path | Content | Purpose |
|------|------|------|------|
| **raw** | `results/raw/{date}/{ticker}.json` | `reports_raw` complete 7-dimension reports + full reasoning + full risk assessment + structured InvestmentDecision | RAG / AI review / strategy backtest / Agent memory |
| **structured** | SQLite cache + `results.json` | Signal, confidence, composite score, 500-char summary | Fast query, sorting, display |
| **summary** | HTML / console | First 500 characters | Terminal display, web browsing |

Each raw JSON file structure:

```json
{
  "ticker": "sh.600519",
  "date": "2026-08-11",
  "analyzed_at": "2026-08-11T20:30:00",
  "elapsed_sec": 12.3,
  "reports_raw": {
    "market": "Complete market report (untruncated)",
    "sentiment": "Complete sentiment report",
    "news": "Complete news report",
    "fundamentals": "Complete fundamentals report",
    "policy": "Complete policy report",
    "hot_money": "Complete capital flow report",
    "lockup": "Complete lock-up report"
  },
  "decision_text": "Complete debate decision text (including Debate process)",
  "risk_assessment": "Complete risk assessment",
  "investment_decision": {
    "signal": "BUY",
    "confidence": 82.0,
    "expected_return": 12.5,
    "thesis": "Core argument summary",
    "risks": ["Valuation risk", "Policy risk"]
  }
}
```

Load raw reports from disk via `TradingAgentsRunner.load_raw_report()`:

```python
from trade_krono_cli.ta_runner import TradingAgentsRunner
runner = TradingAgentsRunner()
raw = runner.load_raw_report("sh.600519", "2026-08-11")
# raw["reports_raw"]["market"] → complete original report text
```

## InvestmentDecision Standardization

TradingAgents' LLM output (free text) is parsed into structured `InvestmentDecision` via `DecisionAdapter`:

```
LLM Free Text
    ↓
DecisionAdapter.parse(text)
    ↓
InvestmentDecision(signal, confidence, expected_return, thesis, risks, ...)
```

### Parsing Priority

| Priority | Strategy | Description |
|--------|------|------|
| 1 | **Rating** field | Match structured fields like `**Rating**: Buy`, directly map signal and base confidence |
| 2 | Negative-context keywords | Check within 10 words before target word for NOT/NO/FAIL etc., avoid misjudgment |
| 3 | fallback | signal=HOLD, confidence=50 |

### Rating → Signal Mapping

| Rating | Signal | Base Confidence |
|--------|--------|-----------|
| Strong Buy | BUY | 95 |
| Buy | BUY | 80 |
| Overweight | BUY | 70 |
| Neutral / Hold | HOLD | 50 |
| Underweight | SELL | 40 |
| Sell | SELL | 30 |
| Strong Sell | SELL | 15 |

### Additional Extracted Fields

- `thesis`: Core argument extracted from `**Investment Thesis**:` or `**Executive Summary**:`
- `risks`: Bullet-list items extracted near risk keywords (Chinese and English supported)
- `expected_return`: Expected return rate parsed from percentage numbers (excluding PE/PEG financial ratio lines)
- `position_size`: Suggested position extracted from patterns like "Position: xx%"

## Security Notes

| Layer | Measure | Location |
|------|------|------|
| Key Management | API keys read only from .env, never hardcoded; supports multiple provider rotation | `security.py::KeyVault` |
| Input Validation | Stock ticker regex (6 digits), date format validation (YYYY-MM-DD) | `security.py::validate_ticker / validate_date` |
| Failure Retry | Exponential backoff retry (TA 3x / Kronos 2x, network/connection errors only) | `security.py::retry` |
| API Rate Limiting | Token bucket algorithm controls baostock request frequency (default 1/sec) | `security.py::TokenBucket` |
| Path Isolation | External projects injected via `sys.path`; output paths restricted to project root | `kronos_runner.py`, `ta_runner.py`, `cli.py::_sanitize_path` |
| Cache Safety | SQLite local storage, no data upload; cache TTL auto-expiry; safe deserialization of `investment_decision` / `prediction_uncertainty` | `cache.py`, `ta_runner.py`, `kronos_runner.py` |
| baostock Login | Global singleton + thread lock to avoid concurrent conflicts | `data.py::_ensure_bs_login` |
| Log Sanitization | Exception logs auto-sanitize API keys (regex replace sk-xxx / Bearer xxx) | `ta_runner.py`, `kronos_runner.py` |

## Notes

1. **First run**: Kronos model loads from local path, takes ~1-3 minutes (faster with GPU)
2. **K-line data**: Fetched free via baostock, max ~100 stocks per day
3. **TA analysis**: Requires LLM API key configured (any one of DeepSeek / OpenAI / Anthropic / MiniMax / Agnes)
4. **GPU inference**: Set `KRONOS_DEVICE=cuda:0` to enable GPU acceleration, requires NVIDIA GPU + CUDA
5. **No source modification**: Called via `sys.path` injection; TradingAgents-astock and Kronos source code is not modified
6. **Caching**: K-line data, TA results, and Kronos predictions are all cached to SQLite, significantly speeding up re-analysis of the same stock on the same date; use `--no-cache` to force a fresh analysis
7. **Ticker format**: Supports `600519`, `sh.600519`, `SZ.000858` etc., auto-normalized
8. **Multi-provider switching**: Switch provider via `LLM_PROVIDER` in `.env`, ensure corresponding API key is configured
9. **Uncertainty quantification**: With default `sample_count=1`, `path_dispersion=null` and `confidence_score` is based only on direction confidence; set `KRONOS_SAMPLE_COUNT>1` to enable cross-sample real uncertainty
10. **baostock login**: Global singleton login with thread lock protection; token bucket rate limiting applied
