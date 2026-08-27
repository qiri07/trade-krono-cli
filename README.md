# trade-krono-cli

> A-Share Research + Kronos Prediction Integrated Pipeline — Parallel Analysis of N Stocks

[![Python](https://img.shields.io/badge/Python-3.12-blue.svg)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

🌐 [中文文档](README_CN.md) | English Docs

## Overview

`trade-krono-cli` is a CLI tool that accepts N A-share stock ticker symbols and **synchronously parallel-calls**:

1. **TradingAgents-astock** — [https://github.com/simonlin1212/TradingAgents-astock](https://github.com/simonlin1212/TradingAgents-astock) multi-Agent deep analysis (market/sentiment/fundamentals/policy/capital/risk debate)
2. **Kronos** — K-line sequence prediction [https://github.com/shiyu-coder/Kronos](https://github.com/shiyu-coder/Kronos) (deep learning-based future price trend prediction with uncertainty quantification)

Results are automatically merged after both complete, producing a ranked report.

## Table of Contents

- [Quick Start](#quick-start)
- [Installation](#installation)
- [Configuration](#configuration)
- [Pipeline Config (YAML/JSON)](#pipeline-config-yamljson)
- [Parameter Priority](#parameter-priority)
- [Usage Guide](#usage-guide)
- [Output Format](#output-format)
- [Architecture](#architecture)
- [External Project Call Path](#external-project-call-path)
- [Prediction Uncertainty Quantification](#prediction-uncertainty-quantification)
- [Scoring Formula](#scoring-formula)
- [Risk Engine](#risk-engine)
- [External Repo Manager](#external-repo-manager)
- [Domain Layer](#domain-layer)
- [Testing](#testing)
- [TA Decision Extraction Logic](#ta-decision-extraction-logic)
- [Three-Tier Raw Report Storage](#three-tier-raw-report-storage)
- [InvestmentDecision Standardization](#investmentdecision-standardization)
- [Security Notes](#security-notes)
- [Degradation & Fallback](#degradation--fallback)
- [Changelog](#changelog)
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

### Install by Feature (Recommended, Significantly Reduces Install Size)

```bash
# TA-only mode: no PyTorch, ~200MB install vs ~2GB+ full install
pip install -e ".[ta]"

# Full install: TA + Kronos prediction + all data sources
pip install -e ".[full]"

# Kronos only (includes PyTorch)
pip install -e ".[kronos]"

# Data sources only (combinable)
pip install -e ".[data,akshare]"
pip install -e ".[data,mootdx]"
pip install -e ".[data,tushare]"

# Development dependencies
pip install -e ".[dev]"
```

### Method 1: uv Virtual Environment (Recommended)

```bash
cd trade-krono-cli
uv venv .venv --python 3.12
source .venv/bin/activate
uv pip install -e ".[dev]"
```

> **Note**: This project requires **Python 3.12**. Python 3.14 is not yet supported (no torch wheel for cp314, and PEP 668 blocks system-wide pip installs).

### Full Install (All Features)

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
| PyTorch | 2.13+ (cu130) | **Optional**: Kronos model inference (`pip install -e ".[kronos]"`) |
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
BACKEND_URL=https://api.example.com/v1  # Backend API URL (optional)
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
| `MAX_DEBATE_ROUNDS` | `1` | Max long-short debate rounds, 0 = no debate |
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

#### Pre-screening Filter (UniverseEngine)

When `--auto-universe` is enabled, the system discovers all A-share stocks and runs them through a multi-stage filter pipeline. Filter parameters can be set via `.env` or CLI:

```bash
# ── Pre-screening Filter Config (.env) ──────────────────────
FILTER_EXCLUDE_ST=true               # Exclude ST/*ST stocks
FILTER_EXCLUDE_LOW_PRICE=true        # Exclude low-price stocks
FILTER_LOW_PRICE_THRESHOLD=3.0       # Low-price threshold (CNY), stocks below this are excluded
FILTER_MIN_PB=                       # Minimum PB ratio (empty = no limit)
FILTER_MARKET_CAP_RANGE=50,5000      # Market cap range (B CNY), format: "min,max"
FILTER_PE_RANGE=                     # PE range, format: "min,max"
FILTER_PB_RANGE=                     # PB range, format: "min,max"
FILTER_INDUSTRY_WHITELIST=           # Industry whitelist, comma-separated
FILTER_INDUSTRY_BLACKLIST=           # Industry blacklist, comma-separated
FILTER_MAX_RISK_SCORE=               # Maximum risk score (0-1)
FILTER_MIN_VOLUME_RATIO=             # Minimum volume ratio
FILTER_MIN_TURNOVER_RATE=            # Minimum turnover rate (%)
```

**Filter Stages:**

| Stage | Description | Typical Reduction |
|-------|-------------|-------------------|
| `StaticFilterStage` | Exclude ST, suspended, delisted, new stocks (<N days), low-price stocks | ~5212 → ~4500 |
| `FundamentalFilterStage` | Filter by market cap / PE / PB / industry whitelist-blacklist | ~4500 → ~2000 |
| `FilterRulesStage` (optional) | User-defined rule chain (supports `<`/`>`/`>=`/`<=`/`==`/`!=`/`in`/`not_in`/`contains`/`match`) | Varies |
| `FactorFilterStage` | Filter by volume ratio / turnover rate for liquidity | ~2000 → ~844 |

> **Note**: `FILTER_*` env vars only take effect in `--auto-universe` mode; normal `--tickers` mode only uses `MIN_CONFIDENCE` / `ALLOWED_SIGNALS`.

**Data Source Options:**

| `--universe-source` | Data Source | Description |
|---------------------|-------------|-------------|
| `akshare` | akshare | Requires `pip install akshare`, some APIs need proxy |
| `mootdx` | mootdx + baostock | Free, no API key required (recommended) |
| `baostock` | baostock | Stock list only, no quote data |

#### Degradation Strategy

When Kronos is unavailable or TA analysis fails, the pipeline degrades gracefully instead of aborting:

| Variable | Default | Description |
|------|--------|------|
| `DEGRADE_MODE` | `strict` | Degradation policy: `strict` / `ta_only_on_kronos_fail` / `ta_cache_fallback` |
| `TA_CACHE_FALLBACK_ENABLED` | `false` | Allow falling back to the latest cached TA result when TA analysis fails (requires `--ta-cache-fallback` flag) |
| `TA_CACHE_MAX_AGE_DAYS` | `7` | Maximum age (days) of a cached TA result before it is considered expired |

- **`strict`** — default; any TA or Kronos error causes that stock to be excluded from the final report.
- **`ta_only_on_kronos_fail`** — if Kronos prediction fails for a stock, the TA result is still included (marked as `⚠ TA-only` in reports).
- **`ta_cache_fallback`** — if TA analysis fails, the system looks up the most recent successful TA result from the research database (marked as `📦 缓存TA`). Requires `--ta-cache-fallback` CLI flag.

> **Note**: A semantic warning is emitted when `TA_CACHE_FALLBACK_ENABLED=true` but `DEGRADE_MODE` is not `ta_cache_fallback`.

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

## Pipeline Config (YAML/JSON)

In addition to `.env` / environment variables, the pipeline accepts a YAML or JSON config file via `--config`:

```bash
.venv/bin/python -m trade_krono_cli.cli run --tickers "600519" --date 2026-08-11 --config pipeline_config.yaml
```

### Full Schema Reference

```yaml
# ── Scoring Weights ────────────────────────────────────────────────────────
scoring:
  ta_confidence_weight:        0.40   # TA confidence × this weight
  change_pct_weight:           0.30   # Expected return × this weight
  direction_base_weight:       0.10   # Direction bonus base weight
  uncertainty_base_weight:     0.10   # Prediction uncertainty base weight
  risk_penalty_weight:         0.15   # Risk penalty × this weight (max deduction)
  direction_bonus_point:       10.0   # UP=+10·0.1=+1  DOWN=-10·0.1=-1
  change_pct_offset:           50.0   # Maps [-50%, +50%] → [0, 100]
  uncertainty_high_threshold:  70.0   # confidence ≥ 70 → +3 bonus
  uncertainty_med_threshold:   50.0   # 50 ≤ confidence < 70 → +1 bonus
  uncertainty_high_bonus:      3.0
  uncertainty_med_bonus:       1.0
  uncertainty_low_penalty:    -2.0   # confidence < 50 → -2 penalty

# ── Risk Engine ────────────────────────────────────────────────────────────
risk:
  weights:
    volatility:     0.30   # 20-day annualized vol → risk
    drawdown:       0.25   # 60-day max drawdown → risk
    liquidity:      0.20   # Avg volume / turnover → risk
    concentration:  0.10   # Placeholder (portfolio weights reserved)
    market_regime:  0.15   # 20d+60d momentum → risk

  volatility:
    low_pct:                  0.0   # 0% vol → 0 risk score
    high_pct:                60.0   # 60% vol → 100 risk score
    insufficient_data_score:  25.0   # < min_rows → this score
    insufficient_data_min_rows: 30

  drawdown:
    breakpoints: [[5, 20], [20, 60], [40, 100]]  # (abs_dd%, risk_score)
    insufficient_data_score:           20.0
    insufficient_data_min_rows:        30

  liquidity:
    breakpoints: [[5, 80], [6, 60], [7, 40], [8, 20]]  # log1p(vol), score
    tail_penalty_rate:     5.0   # log_vol > max_bp after: score -= rate per unit
    insufficient_data_score:           30.0
    insufficient_data_min_rows:        10

  market_regime:
    bear_threshold:    -10.0   # momentum ≤ -10% → 80 risk
    neutral_low:        0.0   # -10% < momentum ≤ 0% → 50-80 risk
    neutral_high:      10.0   # 0% < momentum ≤ 10% → 0-50 risk
    bear_score:        80.0
    neutral_mid_score: 50.0
    bull_base_score:   20.0
    insufficient_data_score:           30.0
    insufficient_data_min_rows:        30

  enable_cost_model:   true   # Deduct transaction costs from expected returns
  commission_bps:      3.0
  slippage_bps:        5.0
  stamp_duty_bps:      1.0

# ── Other Pipeline Settings ─────────────────────────────────────────────────
sample_count:  5
pred_len:      30
lookback:      400
model_name:    kronos-base
device:        cpu
T:             1.0
top_p:         0.9
min_confidence: 55.0
allowed_signals: [BUY, HOLD]
output_dir:    outputs
```

## Parameter Priority

Configuration values follow this precedence (high → low):

```
1. CLI arguments (e.g. --pred-len 60)
2. Environment variables / .env file
3. PipelineConfig YAML/JSON file (via --config)
4. Schema defaults (hard-coded in configs/schema.py)
```

Example — overriding volatility threshold:

```yaml
# pipeline_config.yaml
risk:
  volatility:
    high_pct: 50.0   # 50% vol → 100 risk score (instead of default 60%)
```

```bash
# CLI can override further:
.venv/bin/python -m trade_krono_cli.cli run \
  --tickers "600519" --date 2026-08-11 \
  --config pipeline_config.yaml \
  --pred-len 60          # env var KRONOS_PRED_LEN is ignored when --pred-len is set
```

## Usage Guide

### Command Overview

```
trade-krono-cli run            # One-command run: TA + Kronos parallel pipeline
trade-krono-cli ta             # TradingAgents stock selection analysis only
trade-krono-cli kronos         # Kronos batch prediction only
trade-krono-cli status         # View system status (keys, cache, models)
trade-krono-cli history        # View historical analysis jobs
trade-krono-cli repo status    # View external repo status (branch / commit / lock drift)
trade-krono-cli eval-prediction # Evaluate prediction accuracy on historical data
trade-krono-cli clear-cache    # Clear all caches
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

# ── Full-market auto-discovery (--auto-universe) ──────────────
# Automatically discover all A-share stocks, filter them through multi-stage pipeline,
# then feed results into TA/Kronos analysis
.venv/bin/python -m trade_krono_cli.cli run --auto-universe --universe-source mootdx --date 2026-08-11

# Limit to N stocks after filtering (quick verification)
.venv/bin/python -m trade_krono_cli.cli run --auto-universe --universe-source mootdx --max-tickers 20 --date 2026-08-11

# Override filter conditions via env vars
FILTER_EXCLUDE_LOW_PRICE=false FILTER_MIN_PB=0.5 \
  .venv/bin/python -m trade_krono_cli.cli run --auto-universe --universe-source mootdx --date 2026-08-11
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
| `--degrade-mode` | `strict` | Degradation strategy: `strict` / `ta_only_on_kronos_fail` / `ta_cache_fallback` |
| `--ta-cache-fallback` | `false` | Enable TA cache fallback (requires `--degrade-mode ta_cache_fallback`) |
| `--auto-universe` | `false` | Auto-discover all A-share stocks and filter (ignores --tickers) |
| `--universe-source` | `akshare` | Full-market data source: `akshare` / `mootdx` / `baostock` |
| `--max-tickers` | unlimited | Max stocks to process after filtering (for quick verification) |

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
│   ├── cli.py                  # Typer CLI entry (run / ta / kronos / status / history / eval-prediction / repo / clear-cache)
│   ├── config.py               # Configuration management (.env → Settings singleton)
│   ├── config_validator.py     # Settings validation (15 checks, errors vs warnings)
│   ├── data.py                 # K-line data fetching (baostock)
│   ├── security.py             # Key validation + input validation + retry + rate limiting
│   ├── health.py               # Health checks (LLM API, Kronos import, DB, disk)
│   ├── cache.py                # Cache (TTL performance cache) + ResearchDatabase (persistent records)
│   ├── logger.py               # Logging configuration
│   ├── logging_config.py       # Structured log sinks (text + JSON)
│   ├── globals.py              # Global state cleanup
│   ├── ta_decision.py          # Investment decision standardization (Signal / InvestmentDecision / DecisionAdapter)
│   ├── ta_runner.py            # TradingAgents wrapper (with save_raw_reports three-tier storage)
│   ├── kronos_runner.py        # Kronos prediction wrapper (with prediction_uncertainty module)
│   ├── prediction_eval.py      # Prediction evaluation (Kronos/TA/combined signal win rate validation)
│   ├── pipeline_config.py      # PipelineConfig dataclass + run configuration
│   ├── external.py             # External repo management (repo status/doctor/update/pin)
│   ├── pipeline/               # Pipeline package — unified orchestration entry
│   │   ├── orchestrator.py     # QuantPipeline + PipelineFactory (ThreadPoolExecutor parallel TA+Kronos)
│   │   ├── data_fetcher.py     # Parallel K-line fetching + cache write
│   │   ├── merge.py            # merge_results / filter_pool / default_scorer / run_risk_assessment
│   │   └── reporter.py         # save_json_report / save_html_report / print_results_table / print_results_summary
│   ├── models/                 # Session state models
│   │   ├── kronos_session.py   # Kronos model session lifecycle (lazy-load, device selection)
│   │   └── ta_session.py       # TradingAgents session state (provider, debate rounds)
│   ├── batch/                  # Batch prediction
│   │   └── batch_runner.py     # Async semaphore-based batch Kronos predictions
│   └── risk/                   # Risk engine (volatility/drawdown/liquidity/concentration/regime/gap/event/valuation)
│   ├── domain/                 # Domain model layer (SignalAssessment / InvestmentDecision / Experiment / Evaluation)
│   │   ├── types.py            # Shared enums (Direction, Signal, ExperimentType)
│   │   ├── signal.py           # SignalAssessment + signal conflict detection + EV calculation
│   │   ├── decision.py         # InvestmentDecision
│   │   ├── prediction.py       # TAAnalysis / KronosPrediction / PredictionDistribution
│   │   ├── experiment.py       # Experiment / Hypothesis
│   │   ├── evaluation.py       # EvalRecord / EvaluationSummary / HorizonMetrics
│   │   ├── risk.py             # RiskAssessment
│   │   ├── market.py           # MarketSnapshot
│   │   ├── stock.py            # Stock model
│   │   └── factory.py          # build_* domain object constructors
│   ├── universe/               # Full-market universe discovery & filtering (~5300 → ~844 candidates)
│   │   ├── engine.py           # UniverseEngine: multi-stage pipeline orchestration
│   │   ├── provider.py         # UniverseProvider ABC + data source implementations (akshare/mootdx/baostock)
│   │   └── stages/             # Individual filter stages
│   │       ├── static.py       # StaticFilterStage: ST/suspended/new-stock/low-price filtering
│   │       ├── fundamental.py  # FundamentalFilterStage: PE/PB/market-cap/industry filtering
│   │       ├── factor.py       # FactorFilterStage: liquidity/volume-ratio/turnover filtering
│   │       └── rules.py        # FilterRulesStage: user-defined rule chain
│   ├── pipeline/               # Pipeline package — unified orchestration entry
│   │   ├── orchestrator.py     # QuantPipeline + PipelineFactory (ThreadPoolExecutor parallel TA+Kronos)
│   │   ├── data_fetcher.py     # Parallel K-line fetching + cache write
│   │   ├── merge.py            # merge_results / filter_pool / default_scorer / run_risk_assessment
│   │   ├── reporter.py         # save_json_report / save_html_report / print_results_table / print_results_summary
│   │   ├── resource_manager.py # Per-stock resource lifecycle manager
│   │   └── resource_pool.py    # Shared resource pool (LLM clients, GPU sessions)
│   ├── models/                 # Session state models
│   │   ├── kronos_session.py   # Kronos model session lifecycle (lazy-load, device selection)
│   │   └── ta_session.py       # TradingAgents session state (provider, debate rounds)
│   ├── batch/                  # Batch prediction
│   │   └── batch_runner.py     # Async semaphore-based batch Kronos predictions
├── scripts/
│   └── install.sh              # One-click install script
├── tests/                      # Test suite (1286 tests, mypy clean)
└── external/                   # External project configs (repos.yaml + repo.lock)
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

These tables are used for **historical lookback** — answering "which stocks were analyzed last time?" or "what's the historical signal for a given stock?". Data is never auto-cleaned.

### Parallel Strategy

`pipeline/orchestrator.py` uses `concurrent.futures.ThreadPoolExecutor`:
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

## Risk Engine v2

Multi-dimensional risk quantification for each candidate stock, outputting VaR/CVaR/Beta/volatility/drawdown plus gap/event/valuation regime scores.
Risk is mapped to an **expected return adjustment factor** via exponential decay, replacing the old linear penalty formula.

### Architecture

```
Expected Return
      │
      ▼
  Risk Model
      ├── VaR / CVaR       (tail risk)
      ├── Beta             (systematic risk)
      ├── Volatility       (total risk)
      ├── Max Drawdown     (extreme loss)
      ├── Liquidity        (liquidity risk)
      ├── Gap Risk         (gap risk)
      ├── Event Risk       (event-driven anomaly)
      ├── Valuation Risk   (valuation risk)
      └── Market Regime    (market environment)
```

### Risk Dimensions

| Dimension | Source | Logic | Weight |
|------|----------|------|------|
| **VaR(95%)** | 60-day historical return 5th percentile | Lower-bound estimate of extreme daily loss | Mapped to return adj |
| **CVaR(95%)** | Mean of returns below VaR | Conditional tail loss | Mapped to return adj |
| **Beta** | Cov(stock, market) / Var(market) | >1 high systemic risk, <1 low | Mapped to return adj |
| **Volatility Risk** | 20-day annualized std of daily returns | Higher vol = higher risk (0%→0, 60%→100) | 25% |
| **Drawdown Risk** | 60-day rolling max → max drawdown | Larger DD = higher risk (5%→20, 40%→100) | 20% |
| **Liquidity Risk** | 20-day avg volume + market cap | Lower volume = higher risk (segmented) | 15% |
| **Concentration Risk** | Placeholder | Default 10 points | 8% |
| **Market Regime Risk** | 20-day + 60-day momentum | Downtrend = high, uptrend = low | 12% |
| **Gap Risk** | Frequency of daily moves >3% | More frequent large moves = higher risk | 5% |
| **Event Risk** | Short-term / long-term vol ratio | Ratio >> 1 means recent volatility spike | 5% |
| **Valuation Risk** | PE/PB/market cap composite score | High valuation + small cap = high risk | 5% |

### Output Example

```
============================================
  Risk Metrics for sh.600519 (2026-08-11)
============================================
  VaR(95%)          -2.34%
  CVaR(95%)         -3.12%
  Beta               1.15
  Ann. Volatility   32.5%
  Max Drawdown     -18.3%
--------------------------------------------
  Gap Risk            25
  Event Risk          42
  Valuation Risk      30
  Liquidity Risk      12
  Market Regime       28
--------------------------------------------
  Total Risk         45.2
  Return Adj        -0.062  (-6.2%)
============================================
```

### Relationship to Composite Scoring

Risk Engine v2 bakes risk into **adjusted expected return** rather than applying a flat penalty:

```
adjusted_return = raw_return × (1 + return_adjustment)
                = 15% × (1 - 0.062) ≈ 14.07%
```

When `adjusted_expected_return` is present, the scorer uses it directly;
otherwise it falls back to the legacy linear penalty `-(risk_score/100) × 15`.

### Module Structure

```
trade_krono_cli/risk/
├── models.py          # VaR/CVaR/Beta/Sharpe/return adj/shared weights
├── volatility.py      # Volatility risk
├── drawdown.py        # Drawdown risk
├── liquidity.py       # Liquidity risk
├── concentration.py   # Concentration risk (reserved interface)
├── market_regime.py   # Market regime risk
├── gap_risk.py        # Gap risk
├── event_risk.py      # Event risk
├── valuation_risk.py  # Valuation risk
└── risk_engine.py     # Aggregation engine + RiskScore/RiskMetrics
```

### Usage

```python
from trade_krono_cli.risk import RiskEngine, assess_risk
import pandas as pd

# Method 1: Convenience function (returns RiskScore + RiskMetrics)
engine = RiskEngine()
risk_score, risk_metrics = engine.assess(
    ticker, date, kline_df,
    quote_data={"market_cap": 200.0, "pe_ttm": 18.0, "pb": 2.5}
)
print(risk_metrics.print_report())

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
| `pandas` + `numpy` + `baostock` | A-share data fetching and processing |
| `torch` | **Optional**: Kronos model inference (`[kronos]` group) |
| `pytest` | Test framework (`[dev]` group) |

### External Project Call Path

Both external projects are **called only** (source code is never modified). They are invoked through the unified `cli_anything` namespace package, which consolidates each project's `agent-harness` code under one import path:

```
call chain:
  ta_runner.py  →  from cli_anything.tradingagents.core.analysis import run_analysis, build_config
                   ↑
  source: external/TradingAgents-astock/agent-harness/cli_anything/tradingagents/
           (also available in .venv/lib/python3.12/site-packages/cli_anything/tradingagents/)

  kronos_runner.py → from cli_anything.kronos.utils.kronos_backend import load_model
                     ↑
  source: external/Kronos/agent-harness/cli_anything/kronos/
           (also available in .venv/lib/python3.12/site-packages/cli_anything/kronos/)
```

**Why `agent-harness/`?** Each external project ships two copies of its code:
- Root-level (`tradingagents/`, `model/`) — the original project distribution
- `agent-harness/cli_anything/` — the CLI-facing interface used by trade-krono-cli

trade-krono-cli calls only the `cli_anything.*` paths via `sys.path` injection; the original source code of TradingAgents-astock and Kronos is never read, written, or modified. The `agent-harness/` fallback is used only when the site-packages copy is unavailable.

### External Projects (call-only, source never modified)

| Project | GitHub | Purpose |
|------|--------|------|
| `TradingAgents-astock` | [simonlin1212/TradingAgents-astock](https://github.com/simonlin1212/TradingAgents-astock) | TA multi-Agent deep analysis |
| `Kronos` | [shiyu-coder/Kronos](https://github.com/shiyu-coder/Kronos) | K-line sequence prediction |

Called exclusively via `cli_anything.*` namespace imports (from `agent-harness/` subdirs); **neither project's source code is modified or directly imported**.

## Domain Layer

The `domain/` package defines pure domain objects with no I/O dependencies, enabling clean separation between business logic and persistence.

### Key Modules

| Module | Description |
|--------|-------------|
| `types.py` | Shared enums: `Direction` (UP/DOWN/FLAT), `Signal` (BUY/HOLD/SELL), `ExperimentType` |
| `signal.py` | `SignalAssessment` — merges TA + Kronos signals; signal conflict detection; `_compute_ev()` expected value calculation |
| `decision.py` | `InvestmentDecision` — structured decision with signal, confidence, thesis, risks, invalidations |
| `prediction.py` | `TAAnalysis`, `KronosPrediction`, `PredictionDistribution` — structured prediction data |
| `experiment.py` | `Experiment`, `Hypothesis` — hypothesis-driven experiment tracking |
| `evaluation.py` | `EvalRecord`, `EvaluationSummary`, `HorizonMetrics` — prediction evaluation results |
| `risk.py` | `RiskAssessment`, `RiskFactor` — risk scoring data |
| `market.py` | `MarketSnapshot` — market context data |
| `stock.py` | `Stock` — stock metadata model |
| `factory.py` | `build_*` constructors for creating domain objects from raw data |

### Usage Example

```python
from trade_krono_cli.domain import SignalAssessment, build_signal_assessment
from trade_krono_cli.domain.prediction import TAAnalysis, KronosPrediction
from trade_krono_cli.domain import Direction, Signal

ta = TAAnalysis(ticker="sh.600519", eval_date="2026-08-11",
                signal=Signal.BUY, confidence=80.0, thesis="Strong fundamentals")
kp = KronosPrediction(ticker="sh.600519", eval_date="2026-08-11", horizon=30,
                      direction=Direction.UP, expected_return=3.2, predicted_close=1837.73)
sa = build_signal_assessment("sh.600519", "2026-08-11", ta=ta, kronos=kp)
# sa.conflict == SignalConflict.NO_CONFLICT
# sa.expected_value == 2.56 (computed via _compute_ev)
```

## Testing

```bash
pytest tests/ -v
```

Test Results: **1106 passed** · **87%+ overall coverage** · **mypy clean**

| File | Coverage |
|------|----------|
| `test_cli.py` | CLI entry, parameter parsing, stock list loading, repo command, eval-prediction command |
| `test_data.py` | K-line data fetching, cache read/write, TTL expiry |
| `test_merge.py` | Result merge logic, scoring formula, filter pool |
| `test_pipeline.py` | Pipeline orchestration, error isolation |
| `test_report.py` | JSON/HTML/console report generation |
| `test_security.py` | Key validation, input validation, retry, rate limiting |
| `test_ta_decision.py` | DecisionAdapter structured parsing, InvestmentDecision dataclass, raw report storage |
| `test_research_db.py` | ResearchDatabase full-table CRUD, jobs lifecycle, schema migration, cache/research isolation |
| `test_research_engine.py` | Research database integration (jobs, signals, experiments, walkforward) |
| `test_version.py` | run_id generation, version snapshot construction, config_hash, backward-compatible migration |
| `test_prediction_eval.py` | EvalRecord, EvaluationSummary, HorizonMetrics, statistical calculation logic |
| `test_prediction_eval_ic.py` | IC/rank-IC evaluation metrics |
| `test_risk.py` | Risk Engine (multi-dimensional scores + VaR/CVaR/Beta + RiskMetrics) full-dimension tests |
| `test_risk_models.py` | Risk models (VaR/CVaR/Beta/Sharpe/expected return adj/gap/event/valuation) unit tests |
| `test_external.py` | External repo management (config I/O, status, pin, lock drift detection) |
| `test_kronos_runner.py` | Device resolution (CPU/CUDA/large-model warning), result save, slots cleanup |
| `test_ta_runner.py` | BuildConfig, provider validation, graph lazy-load, batch analysis, raw report I/O |
| `test_committee.py` | Investment Committee LLM deliberation stub |
| `test_signal_lifecycle.py` | Signal state transition tracking |
| `test_resource_manager.py` | Per-stock resource lifecycle |
| `test_resource_pool.py` | Shared resource pool (LLM/GPU) |
| `test_analytics_db.py` | Analytics database operations |
| `test_artifact_manifest.py` | Experiment artifact manifest |
| `test_llm_request.py` | LLM request handling |
| `test_batch_runner.py` | Async semaphore-based batch prediction |
| `integration/test_pipeline_integration.py` | End-to-end pipeline integration |
| `test_config_validator.py` | Settings validation (15 checks: types, ranges, required keys) + new config schema defaults |
| `test_health.py` | Health checks (LLM API, Kronos import, DB, disk space) |
| `test_merge_edge_cases.py` | Merge boundary conditions (constraints, T+1, mixed signals) |
| `test_merge_uncertainty.py` | Uncertainty confidence bonus regression tests |
| `test_pipeline_config.py` | PipelineConfig defaults, override, JSON/YAML roundtrip, scoring & risk schema validation |
| `test_degradation.py` | Degradation modes (strict / ta_only / cache fallback) |
| `test_scoring_plugins.py` | Custom scorer registration and invocation |

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
- `agent_scores` divergence: long-short opinion spread > 20 → confidence -5

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
| Log Sanitization | Exception logs auto-sanitize API keys (regex replace sk-xxx / Bearer xxx) | `security.py::sanitize_for_log` |

---

## Degradation & Fallback

When a stock fails to produce results from either TA or Kronos, the pipeline now degrades gracefully instead of discarding the entire entry.

### Degradation Modes

| Mode | CLI Flag | Behavior |
|------|----------|----------|
| `strict` | (default) | Any TA or Kronos error → stock excluded from final report |
| `ta_only_on_kronos_fail` | `--degrade-mode ta_only_on_kronos_fail` | Kronos failure → TA result kept; TA failure → stock excluded |
| `ta_cache_fallback` | `--degrade-mode ta_cache_fallback --ta-cache-fallback` | TA/Kronos failure → look up latest cached TA result from the research database |

### Report Markers

Degraded stocks are visually distinguished across all output channels:

- **JSON report**: `degradation_mode` field set to `"kronos_degraded"` or `"ta_cache_fallback"`
- **HTML table**: Badge appended next to the ticker — `⚠ TA-only` or `📦 缓存TA`
- **Console table**: Dedicated "降级模式" column shows the same markers
- **Summary**: Counts of degraded and cache-fallback stocks printed alongside the top recommendation

### TA Cache Fallback

When enabled (`--ta-cache-fallback`), failed TA analyses are automatically resolved by querying the research database for the most recent successful TA result for that ticker. The lookup respects:

- `TA_CACHE_MAX_AGE_DAYS` (default: 7) — results older than this are considered expired
- Only records with `error IS NULL` are eligible
- The fallback updates `signal`, `confidence`, and `reasoning` in-place so downstream merge/scoring proceeds normally

## Changelog

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

---

## Notes

1. **First run**: Kronos model loads from local path, takes ~1-3 minutes (faster with GPU)
2. **K-line data**: Fetched free via baostock, max ~100 stocks per day
3. **TA analysis**: Requires LLM API key configured (any one of DeepSeek / OpenAI / Anthropic / MiniMax / Agnes)
4. **GPU inference**: Set `KRONOS_DEVICE=cuda:0` to enable GPU acceleration, requires NVIDIA GPU + CUDA
5. **No source modification**: External projects (TradingAgents-astock, Kronos) are called exclusively via `sys.path` injection into the `cli_anything.*` namespace — their source code is never read, written, or modified
6. **Caching**: K-line data, TA results, and Kronos predictions are all cached to SQLite, significantly speeding up re-analysis of the same stock on the same date; use `--no-cache` to force a fresh analysis
7. **Ticker format**: Supports `600519`, `sh.600519`, `SZ.000858` etc., auto-normalized
8. **Multi-provider switching**: Switch provider via `LLM_PROVIDER` in `.env`, ensure corresponding API key is configured
9. **Uncertainty quantification**: With default `sample_count=1`, `path_dispersion=null` and `confidence_score` is based only on direction confidence; set `KRONOS_SAMPLE_COUNT>1` to enable cross-sample real uncertainty
10. **baostock login**: Global singleton login with thread lock protection; token bucket rate limiting applied
