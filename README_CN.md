# trade-krono-cli

> A股投研 + Kronos 预测一体化流水线 — 并行分析 N 只股票

[![Python](https://img.shields.io/badge/Python-3.12-blue.svg)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

🌐 [English Docs](README.md) | 中文文档

## 概述

`trade-krono-cli` 是一个命令行工具，接受 N 个 A 股股票代码，**同步并行**调用：

1. **TradingAgents-astock** — [https://github.com/simonlin1212/TradingAgents-astock](https://github.com/simonlin1212/TradingAgents-astock) 多 Agent 深度分析（市场/情绪/基本面/政策/资金/风险辩论）
2. **Kronos** — K 线序列预测 [https://github.com/shiyu-coder/Kronos](https://github.com/shiyu-coder/Kronos)（基于深度学习的未来价格走势预测，含不确定性量化）

两者完成后自动合并，输出综合排名报告。

## 目录

- [快速开始](#快速开始)
- [安装](#安装)
- [配置说明](#配置说明)
- [流水线配置（YAML/JSON）](#流水线配置yamljson)
- [参数优先级](#参数优先级)
- [使用指南](#使用指南)
- [输出说明](#输出说明)
- [架构设计](#架构设计)
- [外部项目调用路径](#外部项目调用路径)
- [预测不确定性量化模块](#预测不确定性量化模块)
- [综合打分公式](#综合打分公式)
- [风险引擎（Risk Engine）](#风险引擎risk-engine)
- [外部项目管理（External Repo Manager）](#外部项目管理external-repo-manager)
- [领域层（Domain Layer）](#领域层domain-layer)
- [测试](#测试)
- [TA 决策提取逻辑](#ta-决策提取逻辑)
- [原始报告三层存储](#原始报告三层存储)
- [投资决断标准化（InvestmentDecision）](#投资决断标准化investmentdecision)
- [安全说明](#安全说明)
- [优雅降级与缓存回退](#优雅降级与缓存回退)
- [GitHub Actions 自动化部署](#github-actions-自动化部署)
- [更新日志](#更新日志)
- [注意事项](#注意事项)

---

## 快速开始

```bash
# 安装
uv pip install -e .

# 配置（复制并编辑 .env）
cp .env.example .env

# 克隆外部依赖项目（按需选择，可手动修改路径后 clone）
git clone https://github.com/simonlin1212/TradingAgents-astock external/TradingAgents-astock
git clone https://github.com/shiyu-coder/Kronos external/Kronos

# 创建外部项目配置文件
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

# 一键运行（TA + Kronos 并行）
trade-krono-cli run --tickers "600519,000858,600036" --date 2026-08-11
```

## 安装

### 按需安装（推荐，大幅降低安装体积）

```bash
# TA 分析模式：不含 PyTorch，安装包约 200MB（vs 全量 2GB+）
uv pip install -e ".[ta]"

# 完整安装：TA + Kronos 预测 + 所有数据源
uv pip install -e ".[full]"

# 仅加 Kronos（含 PyTorch）
uv pip install -e ".[kronos]"

# 仅加数据源（可选组合）
uv pip install -e ".[data,akshare]"
uv pip install -e ".[data,mootdx]"
uv pip install -e ".[data,tushare]"

# 开发模式
uv pip install -e ".[dev]"
```

### uv 虚拟环境（推荐）

```bash
cd trade-krono-cli
uv venv .venv --python 3.12
source .venv/bin/activate
uv pip install -e ".[dev]"
```

> **注意**：本项目使用 Python 3.12。Python 3.14 暂不被支持（torch 尚无 cp314 wheel，且 PEP 668 禁止系统 pip 安装）。

### 完整安装（含所有功能）

```bash
cd trade-krono-cli
uv pip install -e .
```

如需开发依赖（测试等）：

```bash
uv pip install -e ".[dev]"
```

### 方式三：一键安装脚本

```bash
bash scripts/install.sh
```

安装脚本会自动：
- 检查 Python 版本（>= 3.10）
- 安装依赖
- 创建 `outputs/` 目录
- 若缺少 `.env`，生成 `.env.example` 模板

### 环境要求

| 依赖 | 最低版本 | 说明 |
|------|----------|------|
| Python | 3.12 | 3.14 暂不支持（torch 无 wheel） |
| PyTorch | 2.13+ (cu130) | **可选**：Kronos 模型推理（`uv pip install -e ".[kronos]"`） |
| Typer | 0.9+ | CLI 框架 |
| Rich | 13+ | 终端美化输出 |
| Python-dotenv | 1.0+ | .env 文件加载 |

## 配置说明

### `.env` 文件

在项目根目录创建 `.env` 文件，所有配置均通过环境变量覆盖，无需修改代码。

```bash
# ── LLM API Key（至少配置一个）────────────────────────────
DEEPSEEK_API_KEY=sk-xxx
# OPENAI_API_KEY=sk-xxx
# ANTHROPIC_API_KEY=sk-ant-xxx
# MINIMAX_API_KEY=xxx
# AGNES_API_KEY=xxx              # Sapiens AI Agnes LLM（可选）

# ── LLM 行为配置 ────────────────────────────────────────
LLM_PROVIDER=deepseek          # 默认 LLM 供应商
DEEP_THINK_LLM=deepseek-chat   # 深度思考模型
QUICK_THINK_LLM=deepseek-chat  # 快速思考模型
BACKEND_URL=https://api.example.com/v1  # 后端 API 地址（可选）
MAX_DEBATE_ROUNDS=1            # 辩论最大轮次
MAX_RISK_DISCUSS_ROUNDS=1      # 风险分析最大轮次
CHECKPOINT_ENABLED=true        # 启用检查点（跳过已完成分析）
OUTPUT_LANGUAGE=Chinese        # 报告输出语言

# ── Kronos 配置 ─────────────────────────────────────────
KRONOS_MODEL=kronos-base       # 模型名称
KRONOS_TOKENIZER=kronos-Tokenizer-base  # Tokenizer 名称
KRONOS_DEVICE=cpu              # cpu / cuda:0（需要 GPU）
KRONOS_LOOKBACK=400            # 历史 K 线回看长度
KRONOS_PRED_LEN=30             # 预测步长
KRONOS_SAMPLE_COUNT=1          # 采样次数（>1 时启用真实不确定性量化）
KRONOS_T=1.0                   # 采样温度
KRONOS_TOP_P=0.9               # nucleus sampling 阈值
KRONOS_USE_SAMPLE_CONFIDENCE=false  # 是否启用基于多 sample 的不确定性量化

# ── 过滤配置 ────────────────────────────────────────────
MIN_CONFIDENCE=55.0            # 最低 TA 置信度阈值
ALLOWED_SIGNALS=BUY,HOLD       # 允许的 TA 信号（逗号分隔）

# ── 数据获取配置 ────────────────────────────────────────
BAOSTOCK_SLEEP_SEC=1.0         # baostock 请求间隔（秒）

# ── 路径配置（默认值通常无需修改）────────────────────────
# TRADINGAGENTS_ROOT=/path/to/TradingAgents-astock
# KRONOS_ROOT=/path/to/Kronos
```

### 配置项详解

#### LLM 配置

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `LLM_PROVIDER` | `deepseek` | 默认供应商：deepseek / openai / anthropic / minimax / agnes |
| `DEEP_THINK_LLM` | `deepseek-chat` | 深度分析 Agent 使用的模型 |
| `QUICK_THINK_LLM` | `deepseek-chat` | 快速分析 Agent 使用的模型 |
| `BACKEND_URL` | — | LLM 后端 API 地址，部分供应商需要配置 |
| `MAX_DEBATE_ROUNDS` | `1` | 多空辩论最大轮次，0 表示不辩论 |
| `MAX_RISK_DISCUSS_ROUNDS` | `1` | 风险分析最大轮次 |
| `CHECKPOINT_ENABLED` | `true` | 启用后跳过已缓存的 TA 分析结果 |
| `OUTPUT_LANGUAGE` | `Chinese` | 报告语言：Chinese / English |

#### Kronos 配置

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `KRONOS_MODEL` | `kronos-base` | 模型名称，需与本地路径一致 |
| `KRONOS_TOKENIZER` | `kronos-Tokenizer-base` | Tokenizer 名称 |
| `KRONOS_DEVICE` | `cpu` | 推理设备，`cpu` 或 `cuda:0` |
| `KRONOS_LOOKBACK` | `400` | 用于预测的历史 K 线根数 |
| `KRONOS_PRED_LEN` | `30` | 预测未来多少根 K 线 |
| `KRONOS_SAMPLE_COUNT` | `1` | 采样次数；`>1` 时取跨样本均值，并计算真实不确定性 |
| `KRONOS_T` | `1.0` | 采样温度，越高越随机 |
| `KRONOS_TOP_P` | `0.9` | Nucleus sampling 阈值 |
| `KRONOS_USE_SAMPLE_CONFIDENCE` | `false` | 是否启用基于多 sample 的真实不确定性量化 |

#### 过滤配置

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MIN_CONFIDENCE` | `55.0` | TA 置信度低于此值的股票不参与综合排名 |
| `ALLOWED_SIGNALS` | `BUY,HOLD` | 只保留信号在此列表中的股票；可选值：`BUY` / `OVERWEIGHT` / `HOLD` / `SELL`（逗号分隔） |

#### 前置市场范围过滤（UniverseEngine）

通过 `--auto-universe` 启动时，系统会从全市场 A 股中逐层过滤，最终产出让 TA/Kronos 消费的股票列表。过滤参数可通过 `.env` 或 CLI 参数设置：

```bash
# ── 前置过滤配置（.env）────────────────────────────────────
FILTER_EXCLUDE_ST=true               # 排除 ST/*ST 股票
FILTER_EXCLUDE_LOW_PRICE=true        # 排除低价股
FILTER_LOW_PRICE_THRESHOLD=3.0       # 低价股阈值（元），低于此价排除
FILTER_MIN_PB=                       # 最低市净率（空=不限制）
FILTER_MARKET_CAP_RANGE=50,5000      # 市值范围（亿元），格式："min,max"
FILTER_PE_RANGE=                     # PE 区间，格式："min,max"
FILTER_PB_RANGE=                     # PB 区间，格式："min,max"
FILTER_INDUSTRY_WHITELIST=           # 行业白名单，逗号分隔
FILTER_INDUSTRY_BLACKLIST=           # 行业黑名单，逗号分隔
FILTER_MAX_RISK_SCORE=               # 风险分上限（0-1）
FILTER_MIN_VOLUME_RATIO=             # 最小量比
FILTER_MIN_TURNOVER_RATE=            # 最小换手率（%）
```

**过滤阶段说明：**

| 阶段 | 说明 | 典型过滤效果 |
|------|------|-------------|
| `StaticFilterStage` | 排除 ST、停牌、退市、次新股（上市不足 N 天）、低价股 | ~5212 → ~4500 |
| `FundamentalFilterStage` | 按市值/PE/PB/行业白黑名单过滤 | ~4500 → ~2000 |
| `FilterRulesStage`（可选） | 用户自定义规则链（支持 `<`/`>`/`>=`/`<=`/`==`/`!=`/`in`/`not_in`/`contains`/`match`） | 按规则进一步过滤 |
| `FactorFilterStage` | 按成交量/换手率过滤流动性不足的股票 | ~2000 → ~844 |

> **注意**：`FILTER_*` 环境变量仅在 `--auto-universe` 模式下生效；普通 `--tickers` 模式下仅使用 `MIN_CONFIDENCE` / `ALLOWED_SIGNALS`。

**数据源选项：**

| `--universe-source` | 数据源 | 说明 |
|---------------------|--------|------|
| `akshare` | akshare | 需 `uv add akshare`，部分接口需要代理 |
| `mootdx` | mootdx + baostock | 免费，无需 API Key（推荐） |
| `baostock` | baostock | 仅获取股票列表，无行情数据 |
| `tonghuashun` | 同花顺（fuyao） | 需配置 `HITHINK_FINANCE_API_KEY`，数据覆盖完整 |

#### 降级策略

当 Kronos 不可用或 TA 分析失败时，流水线会优雅降级而非直接丢弃该股票：

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `DEGRADE_MODE` | `strict` | 降级策略：`strict` / `ta_only_on_kronos_fail` / `ta_cache_fallback` |
| `TA_CACHE_FALLBACK_ENABLED` | `false` | 允许在 TA 失败时回退到最近一次缓存的 TA 结果（需配合 `--ta-cache-fallback` 参数） |
| `TA_CACHE_MAX_AGE_DAYS` | `7` | TA 缓存结果最大有效期（天），超过则视为过期 |

- **`strict`** — 默认；任何 TA 或 Kronos 错误会导致该股票从最终报告中排除。
- **`ta_only_on_kronos_fail`** — 若 Kronos 预测失败，保留 TA 结果；TA 失败则股票被排除。
- **`ta_cache_fallback`** — TA/Kronos 失败时，从研究数据库中查找最近一次成功的 TA 结果（报告中标记为 `📦 缓存TA`）。需配合 `--ta-cache-fallback` CLI 参数启用。

> **注意**：当 `TA_CACHE_FALLBACK_ENABLED=true` 但 `DEGRADE_MODE` 不是 `ta_cache_fallback` 时，校验器会发出语义警告。

#### 路径配置

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `TRADINGAGENTS_ROOT` | — | TradingAgents-astock 项目根目录（可通过 `external/repos.yaml` 替代） |
| `KRONOS_ROOT` | — | Kronos 项目根目录（可通过 `external/repos.yaml` 替代） |

> **提示**：推荐使用 `external/repos.yaml` 管理外部项目路径（见[外部项目管理](#外部项目管理external-repo-manager)），环境变量仅作为后备方案。

### 通过环境变量覆盖

`.env` 中的配置也可以通过命令行环境变量覆盖：

```bash
export LLM_PROVIDER=openai
export KRONOS_DEVICE=cuda:0
trade-krono-cli run --tickers "600519" --date 2026-08-11
```

## 流水线配置（YAML/JSON）

除 `.env` / 环境变量外，流水线支持通过 `--config` 参数加载 YAML 或 JSON 配置文件：

```bash
.venv/bin/python -m trade_krono_cli.cli run --tickers "600519" --date 2026-08-11 --config pipeline_config.yaml
```

### 完整 Schema 参考

```yaml
# ── 综合打分权重 ───────────────────────────────────────────────────────────
scoring:
  ta_confidence_weight:        0.40   # TA 置信度权重
  change_pct_weight:           0.30   # 预期涨跌幅权重
  direction_base_weight:       0.10   # 方向加成基础权重
  uncertainty_base_weight:     0.10   # 预测不确定性基础权重
  risk_penalty_weight:         0.15   # 风险惩罚权重（最高扣 15 分）
  direction_bonus_point:       10.0   # UP=+10·0.1=+1  DOWN=-10·0.1=-1
  change_pct_offset:           50.0   # 将 [-50%, +50%] 映射到 [0, 100]
  uncertainty_high_threshold:  70.0   # confidence ≥ 70 → +3 加分
  uncertainty_med_threshold:   50.0   # 50 ≤ confidence < 70 → +1 加分
  uncertainty_high_bonus:      3.0
  uncertainty_med_bonus:       1.0
  uncertainty_low_penalty:    -2.0   # confidence < 50 → -2 扣分

# ── 风险引擎 ──────────────────────────────────────────────────────────────
risk:
  weights:
    volatility:     0.25   # 20日年化波动率 → 风险分
    drawdown:       0.20   # 60日最大回撤 → 风险分
    liquidity:      0.15   # 日均成交量/换手率 → 风险分
    concentration:  0.08   # 占位（预留组合权重接口）
    market_regime:  0.12   # 20日+60日动量 → 风险分
    gap_risk:       0.05   # 跳空缺口频率 → 风险分
    event_risk:     0.05   # 短期/长期波动率突变 → 风险分
    valuation_risk: 0.05   # PE/PB/市值综合估值风险
    beta:           0.05   # 系统性风险（计入预期收益调整）

  volatility:
    low_pct:                    0.0   # 0% 波动率 → 0 风险分
    high_pct:                  60.0   # 60% 波动率 → 100 风险分
    insufficient_data_score:    25.0   # 数据不足时的默认分
    insufficient_data_min_rows: 30

  drawdown:
    breakpoints: [[5, 20], [20, 60], [40, 100]]  # (绝对回撤%, 风险分)
    insufficient_data_score:           20.0
    insufficient_data_min_rows:        30

  liquidity:
    breakpoints: [[5, 80], [6, 60], [7, 40], [8, 20]]  # log1p(成交量), 风险分
    tail_penalty_rate:     5.0   # log_vol > 最高阈值后每增 1 扣减分数
    insufficient_data_score:           30.0
    insufficient_data_min_rows:        10

  market_regime:
    bear_threshold:    -10.0   # 动量 ≤ -10% → 80 风险分
    neutral_low:        0.0   # -10% < 动量 ≤ 0% → 50-80 风险分
    neutral_high:      10.0   # 0% < 动量 ≤ 10% → 0-50 风险分
    bear_score:        80.0
    neutral_mid_score: 50.0
    bull_base_score:   20.0
    insufficient_data_score:           30.0
    insufficient_data_min_rows:        30

  gap_risk:
    min_gap_pct:              3.0   # 单日涨跌幅超过此阈值视为"缺口"
    insufficient_data_min_rows: 30
    insufficient_data_score:   50.0

  event_risk:
    short_window:             10   # 短期波动率窗口
    long_window:              60   # 长期波动率窗口
    insufficient_data_min_rows: 60
    insufficient_data_score:  50.0

  valuation_risk:
    pe_high:             100.0   # PE > 此值视为高估值风险
    pe_low:               10.0   # PE < 此值视为低估值风险
    pb_high:               5.0   # PB > 此值视为高估值风险
    pb_low:                0.5   # PB < 此值视为低估值风险
    small_cap_threshold:  20.0   # 市值 < 此值（亿元）触发小市值风险

  beta_default:           1.0   # 无市场数据时的默认 Beta
  var_confidence:         0.95  # VaR/CVaR 置信水平
  var_lookback:           60    # VaR 计算回看天数
  enable_cost_model:      true  # 扣除交易成本后再计算预期收益
  commission_bps:         3.0
  slippage_bps:           5.0
  stamp_duty_bps:         1.0

# ── 其他流水线设置 ──────────────────────────────────────────────────────────
sample_count:      5
pred_len:          30
lookback:          400
model_name:        kronos-base
device:            cpu
T:                 1.0
top_p:             0.9
min_confidence:    55.0
allowed_signals:   [BUY, HOLD]
output_dir:        outputs
```

## 参数优先级

配置值遵循以下优先级（高 → 低）：

```
1. CLI 命令行参数（如 --pred-len 60）
2. 环境变量 / .env 文件
3. PipelineConfig YAML/JSON 文件（通过 --config 指定）
4. Schema 默认值（configs/schema.py 中硬编码）
```

示例 — 覆盖波动率阈值：

```yaml
# pipeline_config.yaml
risk:
  volatility:
    high_pct: 50.0   # 50% 波动率 = 100 风险分（原默认 60%）
```

```bash
# CLI 可进一步覆盖：
.venv/bin/python -m trade_krono_cli.cli run \
  --tickers "600519" --date 2026-08-11 \
  --config pipeline_config.yaml \
  --pred-len 60          # 此时环境变量 KRONOS_PRED_LEN 被忽略
```

## 使用指南

### 命令总览

```
trade-krono-cli run            # 一键运行 TA + Kronos 并行流水线
trade-krono-cli ta             # 仅 TradingAgents 选股分析
trade-krono-cli kronos         # 仅 Kronos 批量预测
trade-krono-cli status         # 查看系统状态（密钥、缓存、模型）
trade-krono-cli history        # 查看历史分析作业
trade-krono-cli repo status    # 查看外部 repo 状态（分支 / commit / lock 漂移）
trade-krono-cli eval-prediction # 对历史数据进行预测评估
trade-krono-cli clear-cache    # 清除所有缓存
```

### `run` — 完整流水线

```bash
# 基本用法
trade-krono-cli run --tickers "600519,000858,600036" --date 2026-08-11

# 仅运行 TA 分析（跳过 Kronos）
trade-krono-cli run --tickers "600519,000858" --date 2026-08-11 --skip-kronos

# 自定义置信度阈值和信号过滤
trade-krono-cli run --tickers "600519,000858" --date 2026-08-11 \
  --min-confidence 60 --signals "BUY,HOLD"

# 自定义 Kronos 参数
trade-krono-cli run --tickers "600519" --date 2026-08-11 \
  --pred-len 60 --lookback 800

# 使用配置文件（每行一只股票，支持 # 注释）
cat > stocks.txt << 'EOF'
600519
000858
# 600036  # 注释行
EOF
trade-krono-cli run --config stocks.txt --date 2026-08-11

# 禁用缓存
trade-krono-cli run --tickers "600519" --date 2026-08-11 --no-cache

# ── 全市场自动筛选（--auto-universe）──────────────────────
# 自动发现 A 股市场全部股票，经多阶段过滤后送入 TA/Kronos 分析
trade-krono-cli run --auto-universe --universe-source mootdx --date 2026-08-11

# 限制筛选后最多处理 N 只股票（快速验证）
trade-krono-cli run --auto-universe --universe-source mootdx --max-tickers 20 --date 2026-08-11

# 通过环境变量控制过滤条件（.env 中设置 FILTER_* 变量）
# 或临时覆盖：
FILTER_EXCLUDE_LOW_PRICE=false FILTER_MIN_PB=0.5 \
  trade-krono-cli run --auto-universe --universe-source mootdx --date 2026-08-11
```

**`run` 参数：**

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--tickers, -t` | — | 逗号分隔的股票代码（二选一） |
| `--config, -c` | — | 股票列表文件路径（二选一） |
| `--date, -d` | — | 分析日期 YYYY-MM-DD（必填） |
| `--min-confidence` | `55.0` | 最低 TA 置信度 |
| `--signals` | `BUY,HOLD` | 允许的 TA 信号 |
| `--skip-kronos` | `false` | 跳过 Kronos 预测 |
| `--pred-len` | `30` | Kronos 预测步长 |
| `--lookback` | `400` | Kronos 历史回看长度 |
| `--json` | `outputs/results.json` | JSON 报告输出路径 |
| `--html` | `outputs/report.html` | HTML 报告输出路径 |
| `--no-cache` | `false` | 禁用缓存 |
| `--degrade-mode` | `strict` | 降级策略：`strict` / `ta_only_on_kronos_fail` / `ta_cache_fallback` |
| `--ta-cache-fallback` | `false` | 启用 TA 缓存回退（需配合 `--degrade-mode ta_cache_fallback`） |
| `--auto-universe` | `false` | 自动发现全市场 A 股并筛选（忽略 `--tickers`） |
| `--universe-source` | `akshare` | 全市场数据源：`akshare` / `mootdx` / `baostock` |
| `--max-tickers` | 无限制 | 自动筛选后最多处理的股票数量（用于快速验证） |

### `ta` — 仅 TradingAgents

```bash
trade-krono-cli ta --tickers "600519,000858" --date 2026-08-11
trade-krono-cli ta --tickers "600519" --date 2026-08-11 --output outputs/ta_result.json
```

### `kronos` — 仅 Kronos 预测

```bash
trade-krono-cli kronos --tickers "600519" --date 2026-08-11 --pred-len 60 --lookback 800
```

### `status` — 系统状态

```bash
trade-krono-cli status
```

输出包含：密钥状态、路径配置、缓存统计。

### `clear-cache` — 清除缓存

```bash
trade-krono-cli clear-cache
```

清除 K 线数据、TA 分析和 Kronos 预测的所有缓存。

### `sync-universe` — 全量 A 股 K 线缓存同步

首次运行前需在 `.env` 中配置数据源：

```bash
# 同花顺（推荐，数据覆盖最全）
HITHINK_FINANCE_API_KEY=sk-xxx   # 在 https://fuyao.aicubes.cn/admin 获取
TONGHUASHUN_ENABLED=true
```

```bash
# 首次全量同步（~5500 只 A 股，2 年历史，约 2~3 小时）
trade-krono-cli sync-universe

# 指定 3 年历史
trade-krono-cli sync-universe --lookback 1095

# 使用其他数据源
trade-krono-cli sync-universe --source mootdx
trade-krono-cli sync-universe --source akshare

# 静默模式（不显示进度条，适合脚本）
trade-krono-cli sync-universe --no-progress
```

**增量更新**：后续运行自动跳过已有缓存，仅拉取新增交易日数据（秒级完成）。

## 输出说明

### 控制台输出

```
🚀 启动流水线 3 只 → 2026-08-11
  TA分析 [0/2]
  TA分析 [1/2]
  TA分析 [2/2]
  并行执行 [1/2]
  并行执行 [2/2]
┌──────────┬────────┬────────┬──────────┬───────────┬────────────┬──────────┬──────────┐
│   排名   │  代码  │ TA信号 │  置信度  │ Kronos方向│ 预期涨幅%  │ Kronos置信 │  综合分  │
├──────────┼────────┼────────┼──────────┼───────────┼────────────┼──────────┼──────────┤
│    1     │600519  │  BUY   │   80.0   │    UP     │    3.20    │   72.0   │  82.10   │
│    2     │000858  │  HOLD  │   60.0   │   DOWN    │   -1.50    │   55.0   │  45.00   │
└──────────┴────────┴────────┴──────────┴───────────┴────────────┴──────────┴──────────┘
✅ 完成 → outputs/results.json
```

### JSON 报告

路径：`outputs/results.json`（由 `--json` 参数指定）

```json
[
  {
    "rank": 1,
    "ticker": "sh.600519",
    "ta_signal": "BUY",
    "ta_confidence": 80.0,
    "ta_reasoning": "基本面良好...",
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

### HTML 报告

路径：`outputs/report.html`（由 `--html` 参数指定），自动生成美观的 HTML 表格，鼠标悬停置信度列可查看 direction_confidence / path_dispersion / confidence_score 详细信息。

### 日志

- 流水线日志：`outputs/pipeline.log`
- 内存日志：`outputs/memory_log.jsonl`（每次运行的性能指标）

## 版本追踪

量化系统的核心问题：**半年后重跑，结果不同，但不知道是数据变了、模型变了、还是配置变了。**

每个分析作业自动记录完整的版本快照：

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

| 字段 | 含义 | 何时变化 |
|------|------|---------|
| `run_id` | 唯一运行标识（格式 `YYYYMMDD-HHMMSS-NNN`） | 每次运行自动生成 |
| `data_version` | 数据源快照版本 | 数据源更新或日期变化 |
| `model_versions.kronos` | Kronos 模型 + Tokenizer + 设备 | 更换模型/设备 |
| `model_versions.llm` | LLM 供应商 + 模型 | 切换供应商或模型 |
| `prompt_version` | TA 提示词参数版本 | 辩论轮次/语言配置变化 |
| `strategy_version` | 项目版本（= pyproject.toml version） | 版本升级 |
| `config_hash` | 策略配置 SHA256 前16位 | 任何策略参数变化 |

### 回现示例

```bash
# 查看某只股票的历史信号轨迹（含每次运行的版本信息）
trade-krono-cli history -t sh.600519

# 查看最近所有分析作业（含 run_id 和数据版本）
trade-krono-cli history

# 查看系统状态（含各表的记录数和最近作业的版本摘要）
trade-krono-cli status
```

## 预测评估（Prediction Evaluation）

量化系统的核心：验证预测是否真的有 Alpha。

```bash
# 对历史数据进行预测验证
trade-krono-cli eval-prediction

# 指定日期范围
trade-krono-cli eval-prediction --from 2026-01-01 --to 2026-08-11

# 只评估特定股票
trade-krono-cli eval-prediction -i sh.600519,sz.000858

# 查看已存储的评估结果（不重新计算）
trade-krono-cli eval-prediction --latest
```

评估指标：

| 模块 | 指标 | 说明 |
|------|------|------|
| **Kronos** | 5D/10D/20D 方向准确率 | 预测方向 vs 实际方向（>50% = 超越随机） |
| **Kronos** | MAE / RMSE | 预测涨跌幅 vs 实际涨跌幅的平均误差 |
| **TA BUY** | 胜率 + 平均收益 | 所有 BUY 信号持有 N 天的表现 |
| **TA HOLD** | 平均收益 | 持有基准收益（对照用） |
| **综合信号** | TA BUY + Kronos UP 胜率 | 双重确认信号的表现 |
| **高置信** | 综合分 ≥ 70 的胜率 | 高置信信号是否真的更可靠 |

基准：随机方向准确率 50%，胜率为 50%。

## 架构设计

```
trade-krono-cli
├── trade_krono_cli/
│   ├── cli.py              # Typer CLI 入口（run / ta / kronos / status / history / eval-prediction / repo / clear-cache）
│   ├── config.py           # 配置管理（.env → Settings 单例）
│   ├── config_validator.py # 配置验证（15 项检查，区分错误与警告）
│   ├── data.py             # K 线获取（baostock）
│   ├── security.py         # 密钥校验 + 输入校验 + 重试 + 限流
│   ├── health.py           # 健康检查（LLM API、Kronos 导入、数据库、磁盘）
│   ├── cache.py            # Cache（TTL 性能缓存）+ ResearchDatabase（永久研究记录）
│   ├── logger.py           # 日志配置
│   ├── logging_config.py   # 结构化日志 sink（text + JSON）
│   ├── globals.py          # 全局状态清理
│   ├── ta_decision.py      # 投资决断标准化（Signal / InvestmentDecision / DecisionAdapter）
│   ├── ta_runner.py        # TradingAgents 封装（含 save_raw_reports 三层存储）
│   ├── kronos_runner.py    # Kronos 预测封装（含 prediction_uncertainty 模块）
│   ├── prediction_eval.py  # 预测评估（Kronos/TA/综合信号胜率验证）
│   ├── pipeline_config.py  # PipelineConfig 数据类 + 运行配置
│   ├── external.py         # 外部项目管理（repo status/doctor/update/pin）
│   ├── pipeline/           # 流水线包 — 统一编排入口
│   │   ├── orchestrator.py # QuantPipeline + PipelineFactory（ThreadPoolExecutor 并行 TA+Kronos）
│   │   ├── data_fetcher.py # 并行 K 线获取 + 缓存写入
│   │   ├── merge.py        # merge_results / filter_pool / default_scorer / run_risk_assessment
│   │   └── reporter.py     # save_json_report / save_html_report / print_results_table / print_results_summary
│   ├── models/             # 会话状态模型
│   │   ├── kronos_session.py # Kronos 模型会话生命周期（懒加载、设备选择）
│   │   └── ta_session.py     # TradingAgents 会话状态（供应商、辩论轮次）
│   ├── batch/              # 批量预测
│   │   └── batch_runner.py # 异步信号量控制批量 Kronos 预测
│   └── risk/               # 风险引擎（波动率/回撤/流动性/集中度/市场环境/缺口/事件/估值）
│   ├── domain/                 # 领域模型层（SignalAssessment / InvestmentDecision / Experiment / Evaluation）
│   │   ├── types.py            # 共享枚举（Direction, Signal, ExperimentType）
│   │   ├── signal.py           # SignalAssessment + 信号冲突检测 + EV 计算
│   │   ├── decision.py         # InvestmentDecision
│   │   ├── prediction.py       # TAAnalysis / KronosPrediction / PredictionDistribution
│   │   ├── experiment.py       # Experiment / Hypothesis
│   │   ├── evaluation.py       # EvalRecord / EvaluationSummary / HorizonMetrics
│   │   ├── risk.py             # RiskAssessment
│   │   ├── market.py           # MarketSnapshot
│   │   ├── stock.py            # Stock 模型
│   │   └── factory.py          # build_* 领域对象构造器
│   ├── pipeline/               # 流水线包 — 统一编排入口
│   │   ├── orchestrator.py     # QuantPipeline + PipelineFactory（ThreadPoolExecutor 并行 TA+Kronos）
│   │   ├── data_fetcher.py     # 并行 K 线获取 + 缓存写入
│   │   ├── merge.py            # merge_results / filter_pool / default_scorer / run_risk_assessment
│   │   ├── reporter.py         # save_json_report / save_html_report / print_results_table / print_results_summary
│   │   ├── resource_manager.py # 单只股票资源生命周期管理器
│   │   └── resource_pool.py    # 共享资源池（LLM 客户端、GPU 会话）
│   ├── models/             # 会话状态模型
650→│   │   ├── kronos_session.py # Kronos 模型会话生命周期（懒加载、设备选择）
│   │   └── ta_session.py     # TradingAgents 会话状态（供应商、辩论轮次）
│   ├── batch/              # 批量预测
│   │   └── batch_runner.py # 异步信号量控制批量 Kronos 预测
├── universe/               # 全市场范围发现与过滤（~5300 → ~844 只候选）
│   ├── engine.py           # UniverseEngine：多阶段管道编排主入口
│   ├── provider.py         # UniverseProvider ABC + 各数据源实现（akshare/mootdx/baostock）
│   └── stages/             # 各过滤阶段
│       ├── static.py       # StaticFilterStage：ST/停牌/次新/低价股过滤
│       ├── fundamental.py  # FundamentalFilterStage：PE/PB/市值/行业过滤
│       ├── factor.py       # FactorFilterStage：流动性/量比/换手率过滤
│       └── rules.py        # FilterRulesStage：用户自定义规则链
├── scripts/
│   └── install.sh          # 一键安装脚本
├── tests/                  # 测试套件（2065 项，mypy 零新增错误）
└── external/               # 外部项目配置（repos.yaml + repo.lock）
```

### 缓存 vs 研究数据库

系统使用同一个 SQLite 文件，但概念上分为两层：

**Cache（性能优化，TTL 驱动）**

| 表 | 用途 | TTL |
|----|------|-----|
| `kline_cache` | K 线数据（Pickle 序列化） | 1h（分时）/ 24h（日线） |
| `ta_cache` | TA 分析结果（JSON） | 24h |
| `kronos_cache` | Kronos 预测结果（JSON） | 24h |

这些表用于**加速重复查询**——同一只股票同一日期再次分析时直接返回缓存。TTL 过期后数据可被丢弃。

**ResearchDatabase（永久存储，无 TTL）**

| 表 | 用途 |
|----|------|
| `jobs` | 每次分析作业的元数据（唯一 job_id、日期、股票列表、耗时） |
| `ta_analysis` | TA 分析的结构化摘要（信号、置信度、论点、风险） |
| `kronos_forecast` | Kronos 预测的结构化摘要（方向、预期收益、不确定性） |
| `signals` | 合并后的综合信号（排名、综合分、双源数据） |
| `decisions` | 完整的 `InvestmentDecision` JSON（含 thesis + risks） |
| `raw_reports` | 原始报告文件的磁盘路径索引 |
| `backtest_results` | 策略回测结果（预留） |
| `strategy_runs` | 策略运行记录（预留） |

这些表用于**历史回溯**——回答"上次分析了哪些股票？""某只股票的历史信号是什么？"等问题。数据不会被自动清理。

### 并行策略

`pipeline/orchestrator.py` 使用 `concurrent.futures.ThreadPoolExecutor` 实现：
- TA 分析串行（共享 LLM API，避免并发限流）
- Kronos 预测串行（GPU 模式下避免显存竞争，CPU 模式可考虑并行）
- TA 与 Kronos **异步**执行：两者并行启动，完成后合并打分
- 单只股票失败不影响整体（错误隔离）
- 每次运行自动创建 `jobs` 记录，结果写入 `ta_analysis` / `kronos_forecast` / `signals` / `decisions` 表

## 预测不确定性量化模块

### 背景

原 `confidence_band` 使用单条预测路径的时间步长四分位数（25%/75%）。
当 `sample_count=1`（默认值）时，`q_low == q_high == mean`，**完全没有统计意义**。

重构后引入独立的 **`prediction_uncertainty`** 子模块，替代旧的无意义区间。

### 字段定义

| 字段 | 含义 | 计算方式 | sample_count=1 |
|------|------|---------|----------------|
| `expected_return` | 预期收益率（%） | `(final_close - last_close) / last_close * 100` | ✅ 有效 |
| `direction` | 方向标签 | UP / DOWN / FLAT（±1% 阈值） | ✅ 有效 |
| `direction_confidence` | 方向置信度 | `sigmoid(|change_pct| / (10*std + eps))` ∈ [0,1] | ✅ 有效 |
| `volatility` | 预测路径波动率 | `std(close_values)` | ✅ 有效 |
| `path_dispersion` | 路径分散度 | `std / \|mean\|`（跨样本统计量） | `null`（无统计意义） |
| `confidence_score` | 综合置信评分 | 0–100（见下方公式） | ✅ 有效 |
| `sample_count_used` | 实际样本数 | — | ✅ 记录 |

### confidence_score 计算公式

```
# sample_count = 1（单路径，退化模式）
confidence_score = direction_confidence * 100

# sample_count > 1（多样本，真实不确定性）
confidence_score = min(100, direction_confidence * 50 + max(0, 50 - path_dispersion * 200))
```

### 启用多样本不确定性

```bash
# 方式一：修改 .env
KRONOS_SAMPLE_COUNT=5
KRONOS_USE_SAMPLE_CONFIDENCE=true

# 方式二：环境变量覆盖
export KRONOS_SAMPLE_COUNT=5
trade-krono-cli run --tickers "600519" --date 2026-08-11
```

> **注意**：`sample_count > 1` 时会对每只股票执行多次推理并取均值，推理时间增加约 `sample_count` 倍，但可获得真实的跨路径不确定性估计。

## 综合打分公式

```
score = TA_confidence * 0.4
      + Kronos_change_map * 0.3
      + direction_bonus   * 0.1
      + confidence_score  * 0.1

其中：
- TA_confidence:     0-100，直接映射
- Kronos_change_map: [-50%, +50%] -> [0, 100]（线性映射）
- direction_bonus:   UP = +10，FLAT = 0，DOWN = -10
- confidence_score:  来自 prediction_uncertainty.confidence_score（0-100）
```

综合得分最高者排名最前。相比旧版公式（40%/40%/20%），新版降低了对涨跌幅的权重（40%→30%），引入了不确定性量化加成（10%），使排名更加稳健。

## 风险引擎（Risk Engine v2）

对每只候选股票进行多维度风险量化，输出 VaR/CVaR/Beta/年化波动率/最大回撤等详细指标，
并通过指数衰减模型将综合风险映射为**预期收益调整因子**，替代旧的线性扣分惩罚。

### 架构

```
Expected Return
      │
      ▼
  Risk Model
      ├── VaR / CVaR       （尾部风险度量）
      ├── Beta             （系统性风险）
      ├── Volatility       （总波动率）
      ├── Max Drawdown     （极端损失）
      ├── Liquidity        （流动性风险）
      ├── Gap Risk         （跳空缺口风险）
      ├── Event Risk       （事件驱动异常）
      ├── Valuation Risk   （估值风险）
      └── Market Regime    （市场环境风险）
```

### 风险维度

| 维度 | 计算来源 | 逻辑 | 权重 |
|------|----------|------|------|
| **VaR(95%)** | 60 日历史收益率 5% 分位数 | 极端日损失的下界估计 | 归入预期收益调整 |
| **CVaR(95%)** | VaR 以下收益率的均值 | 尾部条件期望损失 | 归入预期收益调整 |
| **Beta** | 个股与市场收益率协方差 / 市场方差 | >1 高系统风险，<1 低系统风险 | 归入预期收益调整 |
| **波动率风险** | 20 日年化标准差 | 波动率越高风险越大（0%→0，60%→100） | 25% |
| **回撤风险** | 60 日滚动最高价 → 最大回撤 | 回撤越大风险越大（5%→20，40%→100） | 20% |
| **流动性风险** | 20 日平均成交量 + 市值 | 成交量越小风险越大（分段映射） | 15% |
| **集中度风险** | 占位实现 | 当前默认 10 分 | 8% |
| **市场环境风险** | 20 日 + 60 日动量 | 下跌趋势风险高，上涨趋势风险低 | 12% |
| **缺口风险** | 单日涨跌幅 >3% 的频率 | 跳空越大越频繁，风险越高 | 5% |
| **事件风险** | 10 日 / 60 日波动率比值 | 比值 >> 1 表示近期波动异常 | 5% |
| **估值风险** | PE/PB/市值综合评分 | 高估值+小市值 → 高风险 | 5% |

### 输出示例

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

### 与综合打分的关系

Risk Engine v2 将风险**融入预期收益**而非单独扣分：

```
adjusted_return = raw_return × (1 + return_adjustment)
                = 15% × (1 - 0.062) ≈ 14.07%
```

当 `adjusted_expected_return` 存在时，scorer 直接使用调整后的收益率；
否则回退到旧线性惩罚 `-(risk_score/100) × 15`，保持向后兼容。

### 模块结构

```
trade_krono_cli/risk/
├── models.py          # VaR/CVaR/Beta/Sharpe/预期收益调整/共享权重
├── volatility.py      # 波动率风险
├── drawdown.py        # 回撤风险
├── liquidity.py       # 流动性风险
├── concentration.py   # 集中度风险（预留接口）
├── market_regime.py   # 市场环境风险
├── gap_risk.py        # 跳空缺口风险
├── event_risk.py      # 事件驱动异常风险
├── valuation_risk.py  # 估值风险
└── risk_engine.py     # 聚合引擎 + RiskScore/RiskMetrics 数据类
```

### 使用方式

```python
from trade_krono_cli.risk import RiskEngine, assess_risk
import pandas as pd

# 方式一：便捷函数（返回 RiskScore + RiskMetrics）
engine = RiskEngine()
risk_score, risk_metrics = engine.assess(
    ticker, date, kline_df,
    quote_data={"market_cap": 200.0, "pe_ttm": 18.0, "pb": 2.5}
)
print(risk_metrics.print_report())

# 方式二：直接调用子模块
from trade_krono_cli.risk.volatility import calc_volatility_risk
score, ann_vol = calc_volatility_risk(close_series)
```

## 外部项目管理（External Repo Manager）

管理依赖的下游项目（TradingAgents-astock、Kronos），确保结果可复现。

### 为什么需要？

直接引用外部路径（`TRADINGAGENTS_ROOT=/path/to/...`）存在以下问题：

| 问题 | 说明 |
|------|------|
| 路径依赖 | 换机器/目录后配置失效 |
| 版本不可追踪 | 不知道历史结果用的是什么 commit |
| dirty 状态未知 | 本地修改可能导致结果漂移 |
| PyPI 耦合 | `cli-anything-tradingagents` 作为 PyPI 依赖引入，破坏了外部项目的独立性 |

### 文件分工

```
external/
├── repos.yaml    ← 人类可编辑：路径、分支、URL（手动维护）
└── repo.lock     ← 机器维护：锁定的 commit SHA + 时间戳（自动写入）
```

**`repo.lock` 是复现的权威来源。** 每次 `repo pin` 或 `repo update` 后自动写入。
建议将 `repo.lock` 提交到 git（跟踪变更），而 `repos.yaml` 可以只保留路径信息。

### 配置文件示例

```yaml
# external/repos.yaml
repos:
  tradingagents:
    path: external/TradingAgents-astock
    branch: main
    url: https://github.com/simonlin1212/TradingAgents-astock
    commit: null          # null = 跟踪 branch；非 null = pinned 到该 commit
  kronos:
    path: external/Kronos
    branch: main
    url: https://github.com/shiyu-coder/Kronos
    commit: null
```

### repo.lock 示例

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

### CLI 命令

```bash
# 查看所有外部 repo 状态（分支 / commit / locked / dirty / lock 漂移）
trade-krono-cli repo status

# 诊断问题（路径不存在、dirty、lock 漂移、branch mismatch）
trade-krono-cli repo doctor

# 拉取最新代码，自动刷新 repo.lock（仅 unpinned repos）
trade-krono-cli repo update

# 锁定到指定 commit（同时写入 repos.yaml + repo.lock）
trade-krono-cli repo pin tradingagents abc123def456
trade-krono-cli repo pin kronos def789ghi012
```

### 与 PyPI 解耦

`trade-krono-cli` **不再将 `cli-anything-tradingagents` 作为 PyPI 依赖**。

```
旧架构（有耦合）：                    新架构（完全解耦）：
trade-krono-cli                       trade-krono-cli
  ├─ pip install cli-anything-         ├─ external/repos.yaml  （路径+分支）
       tradingagents                   ├─ external/repo.lock   （commit 锁定）
  └─ import tradingagents              └─ sys.path.insert(external/)
```

这样：
- 固定某个 TradingAgents commit 只需用 `repo pin`，无需重新发布 PyPI 包
- 外部项目可以自由修改，不受 `pyproject.toml` 约束
- `repo doctor` 会检测 lock 漂移（代码被更新但 lock 未刷新）

### Pin 的作用

当 `commit` 非 null 时：
1. 每次运行自动检查当前 HEAD 是否与 repo.lock 中记录的一致
2. 不一致时 `repo doctor` 会报 `lock 漂移` 错误
3. 历史分析的 `external_repos` 快照中记录当时使用的 commit
4. 半年后重跑同一脚本，结果完全可复现

### 在版本快照中的体现

每次分析作业的 `external_repos` 字段自动记录：

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

可通过 `trade-krono-cli history -t sh.600519` 查看某只股票历次分析的 external repos 信息。

### 迁移指南

如果当前使用 `TRADINGAGENTS_ROOT` / `KRONOS_ROOT` 环境变量：

```bash
# 1. 将外部项目移到 external/ 目录下（或创建符号链接）
# 示例路径请替换为实际位置
ln -s /你的/路径/TradingAgents-astock external/TradingAgents-astock
ln -s /你的/路径/Kronos external/Kronos

# 2. 创建配置文件
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

# 3. 初始化 lock 文件并验证
trade-krono-cli repo update   # 自动写入 repo.lock
trade-krono-cli repo status
trade-krono-cli repo doctor
```

## 依赖

### Python 依赖（pyproject.toml）

| 包 | 用途 |
|----|------|
| `typer` | CLI 框架 |
| `rich` | 终端美化输出 |
| `loguru` | 日志 |
| `python-dotenv` | .env 加载 |
| `pyyaml` | YAML 配置文件读写 |
| `pandas` + `numpy` + `baostock` | A 股数据获取与数据处理 |
| `torch` | **可选**：Kronos 模型推理（`[kronos]` 组） |
| `pytest` | 测试框架（`[dev]` 组） |

### 外部项目调用路径

两个外部项目均**仅被调用**（源代码从不修改）。它们通过统一的 `cli_anything` 命名空间包进行调用，该包将各自的 `agent-harness` 代码整合到一个 import 路径下：

```
调用链路：
  ta_runner.py  →  from cli_anything.tradingagents.core.analysis import run_analysis, build_config
                   ↑
  源码位置: external/TradingAgents-astock/agent-harness/cli_anything/tradingagents/
           (亦可在 .venv/lib/python3.12/site-packages/cli_anything/tradingagents/ 中找到)

  kronos_runner.py → from cli_anything.kronos.utils.kronos_backend import load_model
                     ↑
  源码位置: external/Kronos/agent-harness/cli_anything/kronos/
           (亦可在 .venv/lib/python3.12/site-packages/cli_anything/kronos/ 中找到)
```

**为何使用 `agent-harness/`？** 每个外部项目包含两份代码：
- 根目录级（`tradingagents/`、`model/`）— 原始项目发行版
- `agent-harness/cli_anything/` — CLI 面向的接口层

trade-krono-cli 仅通过 `sys.path` 注入调用 `cli_anything.*` 路径；TradingAgents-astock 和 Kronos 的原始源代码**不会被读取、写入或修改**。`agent-harness/` 仅在 site-packages 副本不可用时作为后备方案。

### 外部项目（仅调用，从不修改源码）

| 项目 | GitHub | 用途 |
|------|--------|------|
| `TradingAgents-astock` | [simonlin1212/TradingAgents-astock](https://github.com/simonlin1212/TradingAgents-astock) | TA 多 Agent 深度分析 |
| `Kronos` | [shiyu-coder/Kronos](https://github.com/shiyu-coder/Kronos) | K 线序列预测 |

仅通过 `cli_anything.*` 命名空间导入调用（来自 `agent-harness/` 子目录）；**两个项目的源代码均不被修改或直接导入**。

## 领域层（Domain Layer）

`domain/` 包定义了纯领域对象，无 I/O 依赖，实现业务逻辑与持久化层的清晰分离。

### 核心模块

| 模块 | 说明 |
|------|------|
| `types.py` | 共享枚举：`Direction`（UP/DOWN/FLAT）、`Signal`（BUY/HOLD/SELL）、`ExperimentType` |
| `signal.py` | `SignalAssessment` — 合并 TA + Kronos 信号；信号冲突检测；`_compute_ev()` 期望值计算 |
| `decision.py` | `InvestmentDecision` — 结构化决策（信号、置信度、论点、风险、失效条件） |
| `prediction.py` | `TAAnalysis`、`KronosPrediction`、`PredictionDistribution` — 结构化预测数据 |
| `experiment.py` | `Experiment`、`Hypothesis` — 基于假设的实验追踪 |
| `evaluation.py` | `EvalRecord`、`EvaluationSummary`、`HorizonMetrics` — 预测评估结果 |
| `risk.py` | `RiskAssessment`、`RiskFactor` — 风险评分数据 |
| `market.py` | `MarketSnapshot` — 市场环境快照 |
| `stock.py` | `Stock` — 股票元数据模型 |
| `factory.py` | `build_*` 构造函数，从原始数据创建领域对象 |

### 使用示例

```python
from trade_krono_cli.domain import SignalAssessment, build_signal_assessment
from trade_krono_cli.domain.prediction import TAAnalysis, KronosPrediction
from trade_krono_cli.domain import Direction, Signal

ta = TAAnalysis(ticker="sh.600519", eval_date="2026-08-11",
                signal=Signal.BUY, confidence=80.0, thesis="基本面良好")
kp = KronosPrediction(ticker="sh.600519", eval_date="2026-08-11", horizon=30,
                      direction=Direction.UP, expected_return=3.2, predicted_close=1837.73)
sa = build_signal_assessment("sh.600519", "2026-08-11", ta=ta, kronos=kp)
# sa.conflict == SignalConflict.NO_CONFLICT
# sa.expected_value == 2.56（通过 _compute_ev 计算）
```

## 测试

```bash
pytest tests/ -v
```

测试结果：**1106 项通过** · **87%+ 整体覆盖** · **mypy 零错误**

| 文件 | 覆盖模块 |
|------|----------|
| `test_cli.py` | CLI 入口、参数解析、股票列表加载、repo 命令、eval-prediction 命令 |
| `test_data.py` | K 线数据获取、缓存读写、TTL 过期 |
| `test_merge.py` | 结果合并逻辑、打分公式、过滤池 |
| `test_pipeline.py` | 流水线编排、错误隔离 |
| `test_report.py` | JSON/HTML/控制台报告生成 |
| `test_security.py` | 密钥校验、输入校验、重试、限流 |
| `test_ta_decision.py` | DecisionAdapter 结构化解析、InvestmentDecision 数据类、raw 报告存储 |
| `test_research_db.py` | ResearchDatabase 全表 CRUD、jobs 生命周期、schema 迁移、cache/research 隔离 |
| `test_research_engine.py` | 研究数据库集成（jobs、signals、experiments、walkforward） |
| `test_version.py` | run_id 生成、版本快照构建、config_hash、向后兼容迁移 |
| `test_prediction_eval.py` | EvalRecord、EvaluationSummary、HorizonMetrics、统计计算逻辑 |
| `test_prediction_eval_ic.py` | IC/ rank-IC 评估指标 |
| `test_risk.py` | 风险引擎（多维度风险评分 + VaR/CVaR/Beta + RiskMetrics）全维度测试 |
| `test_risk_models.py` | 风险模型（VaR/CVaR/Beta/Sharpe/预期收益调整/缺口/事件/估值）专项测试 |
| `test_external.py` | 外部项目管理（config I/O、status、pin、lock 漂移检测） |
| `test_kronos_runner.py` | 设备解析（CPU/CUDA/大模型警告）、结果保存、slots 清理 |
| `test_ta_runner.py` | BuildConfig、provider 校验、图懒加载、批量分析、raw 报告读写 |
| `test_committee.py` | 投资委员会 LLM 审议存根 |
| `test_signal_lifecycle.py` | 信号状态流转追踪 |
| `test_resource_manager.py` | 单只股票资源生命周期 |
| `test_resource_pool.py` | 共享资源池（LLM/GPU） |
| `test_analytics_db.py` | 分析数据库操作 |
| `test_artifact_manifest.py` | 实验产物清单 |
| `test_llm_request.py` | LLM 请求处理 |
| `test_batch_runner.py` | 异步信号量控制批量预测 |
| `integration/test_pipeline_integration.py` | 端到端流水线集成测试 |
| `test_config_validator.py` | 配置校验（15项检查：类型、范围、必填字段）+ 新配置 schema 默认值验证 |
| `test_health.py` | 健康检查（LLM API、Kronos 导入、数据库、磁盘空间） |
| `test_merge_edge_cases.py` | 合并边界条件（约束、T+1、混合信号） |
| `test_merge_uncertainty.py` | 不确定性置信度映射回归测试 |
| `test_pipeline_config.py` | PipelineConfig 默认值、覆盖、JSON/YAML 往返、scoring & risk schema 校验 |
| `test_degradation.py` | 降级模式（strict / ta_only / cache fallback） |
| `test_scoring_plugins.py` | 自定义打分插件注册与调用 |

## TA 决策提取逻辑

TA 分析结果中的 `signal` 和 `confidence` 由 `_extract_decision()` 从 `final_state` 中提取，采用三级策略：

```
优先级 1: **Rating**: <value> 结构化字段
  → 直接映射信号 + 基础置信度
  → 支持: Strong Buy/Buy/Overweight/Neutral/Hold/Underweight/Sell/Strong Sell

优先级 2: 负上下文感知关键词匹配
  → 先检查目标词前 5 个词内是否有否定词（NOT/NO/FAIL 等）
  → 避免 "not recommend BUY" 误判为 BUY

优先级 3: fallback
  → signal=HOLD, confidence=50
```

置信度微调：
- `position_size` 佐证：持仓比例越大，确认信号时 confidence +5
- `agent_scores` 分歧：多空意见 spread > 20 时 confidence -5

| Rating | Signal | 基础置信度 |
|--------|--------|-----------|
| Strong Buy | BUY | 95 |
| Buy | BUY | 80 |
| Overweight | BUY | 70 |
| Neutral / Hold | HOLD | 50 |
| Underweight | SELL | 40 |
| Sell | SELL | 30 |
| Strong Sell | SELL | 15 |

## 原始报告三层存储

为解决 LLM 报告被截断导致历史信息永久丢失的问题，系统采用三层存储架构：

```
outputs/results/raw/{date}/{ticker}.json    ← raw: 完整原始报告（永不截断）
outputs/results/results.json                 ← structured: 核心字段 + 摘要（SQLite 缓存）
outputs/report.html                          ← summary: 500字展示层
```

| 层级 | 路径 | 内容 | 用途 |
|------|------|------|------|
| **raw** | `results/raw/{date}/{ticker}.json` | `reports_raw` 完整7维度报告 + 完整 reasoning + 完整风险评估 + 结构化 InvestmentDecision | RAG / AI复盘 / 策略回测 / Agent memory |
| **structured** | SQLite 缓存 + `results.json` | 信号、置信度、综合分、500字摘要 | 快速查询、排序、展示 |
| **summary** | HTML / 控制台 | 前500字摘要 | 终端展示、网页浏览 |

每份 raw JSON 文件结构：

```json
{
  "ticker": "sh.600519",
  "date": "2026-08-11",
  "analyzed_at": "2026-08-11T20:30:00",
  "elapsed_sec": 12.3,
  "reports_raw": {
    "market": "完整市场报告（无截断）",
    "sentiment": "完整情绪报告",
    "news": "完整新闻报告",
    "fundamentals": "完整基本面报告",
    "policy": "完整政策报告",
    "hot_money": "完整资金报告",
    "lockup": "完整限售报告"
  },
  "decision_text": "完整辩论决策文本（含 Debate 过程）",
  "risk_assessment": "完整风险评估",
  "investment_decision": {
    "signal": "BUY",
    "confidence": 82.0,
    "expected_return": 12.5,
    "thesis": "核心论点摘要",
    "risks": ["估值风险", "政策风险"]
  }
}
```

通过 `TradingAgentsRunner.load_raw_report()` 可从磁盘加载原始报告：

```python
from trade_krono_cli.ta_runner import TradingAgentsRunner
runner = TradingAgentsRunner()
raw = runner.load_raw_report("sh.600519", "2026-08-11")
# raw["reports_raw"]["market"] → 完整报告原文
```

## 投资决断标准化（InvestmentDecision）

TradingAgents 的 LLM 输出（自由文本）通过 `DecisionAdapter` 解析为结构化 `InvestmentDecision`：

```
LLM 自由文本
    ↓
DecisionAdapter.parse(text)
    ↓
InvestmentDecision(signal, confidence, expected_return, thesis, risks, ...)
```

### 解析优先级

| 优先级 | 策略 | 说明 |
|--------|------|------|
| 1 | **Rating** 字段 | 匹配 `**Rating**: Buy` 等结构化字段，直接映射信号和基础置信度 |
| 2 | 负上下文感知关键词 | 检查目标词前 10 词内是否有 NOT/NO/FAIL 等否定词，避免误判 |
| 3 | fallback | signal=HOLD, confidence=50 |

### Rating → Signal 映射表

| Rating | Signal | 基础置信度 |
|--------|--------|-----------|
| Strong Buy | BUY | 95 |
| Buy | BUY | 80 |
| Overweight | BUY | 70 |
| Neutral / Hold | HOLD | 50 |
| Underweight | SELL | 40 |
| Sell | SELL | 30 |
| Strong Sell | SELL | 15 |

### 额外提取字段

- `thesis`：从 `**Investment Thesis**:` 或 `**Executive Summary**:` 提取核心论点
- `risks`：从风险关键词附近提取 bullet-list 条目（中英文支持）
- `expected_return`：从百分比数字中解析预期收益率（排除 PE/PEG 等财务比率行）
- `position_size`：从 "仓位: xx%" 等模式提取建议仓位

## 安全说明

| 层面 | 措施 | 位置 |
|------|------|------|
| 密钥管理 | API key 仅从 .env 读取，不硬编码；支持多个供应商轮换 | `security.py::KeyVault` |
| 输入校验 | 股票代码正则匹配（6 位数字）、日期格式校验（YYYY-MM-DD） | `security.py::validate_ticker / validate_date` |
| 失败重试 | 指数退避重试（TA 3次 / Kronos 2次，仅重试网络/连接错误） | `security.py::retry` |
| API 限流 | 令牌桶算法控制 baostock 请求频率（默认 1次/秒） | `security.py::TokenBucket` |
| 路径隔离 | 外部项目通过 `sys.path` 注入，输出路径限制在项目根目录下 | `kronos_runner.py`, `ta_runner.py`, `cli.py::_sanitize_path` |
| 缓存安全 | SQLite 本地存储，不上传任何数据；缓存 TTL 过期自动清理；`investment_decision` / `prediction_uncertainty` 缓存反序列化安全处理 | `cache.py`, `ta_runner.py`, `kronos_runner.py` |
| baostock 登录 | 全局单例 + 线程锁，避免并发冲突 | `data.py::_ensure_bs_login` |
| 日志脱敏 | 异常日志自动脱敏 API key（正则替换 sk-xxx / Bearer xxx） | `security.py::sanitize_for_log` |

---

## 优雅降级与缓存回退

当 TA 或 Kronos 分析失败时，流水线会优雅降级，而非直接丢弃该股票。

### 降级策略

| 策略 | CLI 参数 | 行为 |
|------|----------|------|
| `strict` | （默认） | TA 或 Kronos 任意失败 → 股票从报告中排除 |
| `ta_only_on_kronos_fail` | `--degrade-mode ta_only_on_kronos_fail` | Kronos 失败 → 保留 TA 结果；TA 失败 → 股票被排除 |
| `ta_cache_fallback` | `--degrade-mode ta_cache_fallback --ta-cache-fallback` | TA/Kronos 失败 → 从研究数据库查找最近一次成功的 TA 结果 |

### 报告标记

降级股票在所有输出渠道中均有视觉区分：

- **JSON 报告**：`degradation_mode` 字段设为 `"kronos_degraded"` 或 `"ta_cache_fallback"`
- **HTML 表格**：股票代码旁附加徽章 — `⚠ TA-only` 或 `📦 缓存TA`
- **控制台表格**：新增"降级模式"列显示相同标记
- **摘要输出**：在最佳推荐旁打印降级和缓存回退股票计数

### TA 缓存回退

启用后（`--ta-cache-fallback`），失败的 TA 分析会自动查询研究数据库，查找该股票代码最近一次成功的 TA 结果。查找规则：

- `TA_CACHE_MAX_AGE_DAYS`（默认：7）— 超过此时限的缓存结果视为过期
- 仅查询 `error IS NULL` 的记录
- 回退结果会原地更新 `signal`、`confidence` 和 `reasoning`，确保后续合并打分流程正常执行

## 更新日志

### v0.1.7 — 2026-08-27

**GitHub Actions CI/CD 与测试覆盖扩展：**

- **新增 `.github/workflows/daily-run.yml`**：每日投研流水线，定时触发（UTC 07:30 = 北京时间 15:30，工作日）+ 手动触发（9 个可配置参数：股票代码、日期、置信度、信号过滤、全市场筛选、跳过 Kronos 等）
- **新增 `.github/workflows/ci.yml`**：CI 矩阵（lint / type-check / test），pytest 覆盖率报告上传 Codecov
- **新增 `scripts/_gh_summary.py`**：GitHub Actions 运行结果摘要脚本
- **测试覆盖扩展**：1411 → **2065 项**（+654 新增）：
  - `test_config_output.py` — `OutputConfig` 默认值、merge、roundtrip（6 项）
  - `test_config_abnormality.py` — `AbnormalityConfig` 默认值、校验、merge 边界（14 项）
  - `test_config_filters.py` — `FilterConfig` 全字段组合的 validate/merge（22 项）
  - `test_prediction_distribution.py` — `compute_single_sample`、`compute_multi_sample`、`build_distribution`、分位数逻辑（25 项）
  - `test_data_snapshot.py` — `DataSourceSnapshot`、`DataSnapshot`、`filter_kline_to_cut_date`（含冻结 dataclass、未来数据检测、copy 语义，20 项）
- 同步更新 README.md / README_CN.md 架构图中测试数量

---

### v0.1.6 — 2026-08-14

**Bug 修复与并发安全加固：**

- **修复 `KronosSession` 构造参数错误**：`KronosRunner` 不接受 `model_name`/`device` 参数，改为传递 `session=self`（`models/kronos_session.py`）
- **修复 `ta_runner.py` 变量作用域 Bug**：`_analyze_one_impl` 的 `finally` 块引用了外层方法定义的 `t0`，将其提升到 `_analyze_one_impl` 方法内部
- **修复 `trade_date` 缺失问题**：`build_config` 返回值不含 `trade_date`，在调用适配器前显式注入
- **修复 `HorizonMetrics` 缺少动态属性**：为 `eval_data.py` 添加 `rank_ic_*`、`ic_*`、`sortino_ratio`、`calmar_ratio`、`turnover`、`alpha_vs_benchmark` 等字段的默认值 0.0
- **修复 `EvaluationSummary` 缺少 IC 聚合字段**：新增 `ic_composite_rank_mean`、`ic_kronos_rank_mean`、`alpha_best_benchmark` 等字段，并在 `_compute_summary` 中调用 `compute_ic_metrics`
- **修复 `FailureStore` 多线程竞态**：添加 `threading.Lock` 保护 `record()` 和 `_save()` 操作
- **扩展 Session 缓存 key**：`KronosSession` 纳入 `T`/`top_p`/`lookback`，`TASession` 纳入 `max_debate_rounds`/`output_language`
- **修复 SELL 信号 `expected_return` 符号反转**：移除 `-pct` 的负号操作
- **修复 `run` 命令 `--config` 参数冲突**：股票列表文件参数改为 `--stock-file`/`-f`，Pipeline 配置文件保持 `--config`/`-c`
- **修复 `kronos` 命令缺少 `ta_cache_fallback` 参数**：补齐降级策略 CLI 接口
- **SQLite 连接添加 timeout**：`cache.py` 中 `connect()` 增加 `timeout=10.0`
- **测试数量**：**1164 项通过**（从 1106 增长），0 失败

---

### v0.1.5 — 2026-08-14

**领域层重构与代码质量提升：**
- **新增 `domain/` 领域层**：`SignalAssessment`、`InvestmentDecision`、`Experiment`、`Evaluation`、`PredictionDistribution`、`RiskAssessment` 均为纯领域对象（无 I/O，无 SQLite 耦合）
- **消除重复代码**：移除重复的 `SignalConflict` 枚举、`_compute_ev` 函数、`ExperimentType`/`Hypothesis` 类，统一从 `domain/` 导入
- **清理 `kronos_runner.py`**：移除重复的 `prediction_distribution` 字段，统一通过 `prediction_uncertainty` 模块处理不确定性
- **删除 `research_db.py` 中未使用的方法**：移除未被调用的 `insert_signal_assessment`、`insert_decision_domain`、`insert_experiment_domain`（减少约 103 行）
- **新增流水线资源管理**：`resource_manager.py` 和 `resource_pool.py`，管理 LLM 客户端和 GPU 会话生命周期
- **新增 `signal_lifecycle.py`**：追踪信号跨次运行的状态演变
- **新增 `experiment_registry.py`**：基于假设的实验追踪，支持通过/失败评估
- **新增 `committee.py`**：LLM 驱动的投资委员会审议存根
- **新增分析与评估模块**：`analytics_db.py`、`artifact_manifest.py`、`eval_ic.py`、`eval_benchmark.py`、`eval_walkforward.py`
- **测试数量**：**1106 项通过**（从 468 增长）

---

### v0.1.4 — 2026-08-13

**优雅降级机制：**
- 三种降级模式：`strict`（默认）、`ta_only_on_kronos_fail`、`ta_cache_fallback`
- Kronos 不可用时，TA-only 结果仍保留在报告中（标记为 `⚠ TA-only`）
- TA 分析失败时，可回退到研究数据库中最近一次成功的缓存 TA 结果（标记为 `📦 缓存TA`）
- 新增 CLI 参数：`--degrade-mode` 和 `--ta-cache-fallback`
- 新增环境变量：`DEGRADE_MODE`、`TA_CACHE_FALLBACK_ENABLED`、`TA_CACHE_MAX_AGE_DAYS`
- 配置校验器在 `TA_CACHE_FALLBACK_ENABLED=true` 但 `DEGRADE_MODE` 不匹配时发出语义警告
- 提取了 orchestrator 中的 `_apply_ta_cache_fallback()`、reporter 中的 `_degradation_badge()`、cli_commands 中的 `_build_degrade_overrides()` 辅助函数
- 测试数量：**468**（从 459 增长）；新增 `tests/test_degradation.py`，共 33 项测试

---

### v0.1.3 — 2026-08-13

**配置集中校验与分层管理：**

- 新增 `configs/schema.py` — 所有打分权重、风险维度权重、分段映射阈值（波动率 0%→0 / 60%→100、回撤断点、流动性 log 阈值、市场环境动量阈值）统一在此定义，均为不可变 dataclass，支持 `validate()` 和 `merge()`
- `RiskEngine` 改为接受 `RiskConfig`；所有 `calc_*_risk()` 函数增加可选 `thresholds` 参数
- `default_scorer()` / `merge_results()` 增加可选 `ScoringConfig` / `RiskConfig` 参数
- `PipelineConfig` 携带 `scoring: ScoringConfig` 和 `risk: RiskConfig` 字段；`from_dict()` 正确还原嵌套 dataclass
- `cli_commands._load_env()` 在启动时调用 `run_validation()` — 致命错误直接退出，警告降级输出
- `config_validator.validate_settings()` 返回 `(errors, warnings)` 元组
- **优先级文档化**：CLI 参数 > 环境变量/.env > PipelineConfig YAML > Schema 默认值
- YAML 序列化修复：改用 `yaml.safe_dump`（不再产生 `!!python/tuple` 标签）；`to_dict()` 递归转换 tuple→list
- 测试数量：**468**（从 459 增长）；新增所有风险模块自定义阈值测试、`test_from_dict_restores_dataclasses`、`test_merge_works_with_loaded_config`

---

**流水线收敛重构：**
- 删除冗余的根目录 `merge.py`、`report.py`、`pipeline.py` 及 `pipeline/scorer.py`
- 将合并/打分/报告逻辑统一收敛到 `pipeline/merge.py` 和 `pipeline/reporter.py`
- `pipeline/__init__.py` 现只导出 `QuantPipeline` 和 `PipelineFactory`，子模块内部直接导入
- 移除未使用的 `MergedItem` dataclass、生产代码中从未调用的 `score_merged_results`，以及 `orchestrator.py` 中的 4 个无效导入
- `filter_pool` 现直接返回 `list[StockAnalysisResult]`，不再通过 dict 包装泄露内部结构
- 测试数量：**459**（从 398 增长）；覆盖率保持 **87%**

---

### v0.1.1 — 2026-08-12

**代码质量与静态分析：**
- **mypy 零错误**：38 个源文件全部通过类型检查；修复了 `kronos_runner.py`、`ta_runner.py`、`batch_runner.py`、`cache.py`、`errors.py`、`external.py`、`trading_constraints.py`、`prediction_eval.py`、`pipeline/orchestrator.py`、`logging_config.py` 中的类型注解问题
- **覆盖率 87%**（从 80% 提升）：`prediction_eval.py` 94%、`ta_runner.py` 91%、`kronos_runner.py` 83%、`cli.py` 56%
- 测试数量：**459**（从 398 增长）；新增 CLI 入口、kronos_runner 设备解析、ta_runner 配置/校验/图、prediction_eval 边界情况、batch runner、config_validator、health 检查、merge 边界/不确定性用例、流水线收敛重构

**新包结构（模块化重构）：**
- `pipeline/` — orchestrator（ThreadPoolExecutor + raw 报告自动保存）、data_fetcher（并行 K 线获取）、merge（打分 + 风险惩罚 + 排名）、reporter（JSON/HTML/控制台）
- `models/` — kronos_session（懒加载、设备选择）、ta_session（供应商/辩论状态）
- `batch/` — batch_runner（异步信号量控制批量预测）

**新增测试文件：** `test_cli.py`、`test_kronos_runner.py`、`test_ta_runner.py`、`test_prediction_eval.py`、`test_batch_runner.py`、`test_config_validator.py`、`test_health.py`、`test_merge_edge_cases.py`、`test_merge_uncertainty.py`、`integration/test_pipeline_integration.py`、`test_pipeline_config.py`

---

### v0.1.0 — 2026-08-12

**代码质量与安全修复：**
- 在 `security.py` 新增 `sanitize_for_log()` — 统一的 API key 脱敏工具函数，替换 `kronos_runner.py` 和 `ta_runner.py` 中原有的重复内联正则
- 在 `security.py` 新增 `ensure_import_path()` — 统一 harness 优先的 sys.path 注入逻辑，消除两个 runner 模块中的重复代码
- 修复 `ta_runner.py` 中 `_TRAIDINGAGENTS_IMPORTED` 拼写错误 → `_TRADINGAGENTS_IMPORTED`
- 将魔法截断字面量（`[:500]`、`[:300]`）提取为模块级常量，分布于 `ta_runner.py`、`pipeline/merge.py`、`ta_decision.py`、`cache.py`
- 重构 `EvaluationSummary` — 用按 horizon 分组的 `HorizonMetrics` dataclass 替代原有 30+ 个平铺字段；同步更新所有调用方
- 修复 `cache.py` 中 SQL f-string 表名插值问题 — 新增 `_validate_table_name()` 白名单校验助手
- 移除 `HorizonMetrics` 上重复的 `@dataclass` 装饰器

**测试：** `test_security.py` 新增 4 个，`test_prediction_eval.py` 新增 2 个，总计 162 个测试全部通过。

---

## GitHub Actions 自动化部署

本项目提供两套 GitHub Actions workflow，支持每日定时自动投研分析和持续集成。

### 工作流文件

| 文件 | 说明 |
|------|------|
| `.github/workflows/daily-run.yml` | 每日投研分析流水线（定时 + 手动触发） |
| `.github/workflows/ci.yml` | 持续集成（lint / type-check / test） |

### 自动分析流水线 (`daily-run.yml`)

**触发方式：**
- **定时触发**：每个交易日北京时间 15:30（UTC 07:30）自动运行
- **手动触发**：通过 GitHub Actions 面板手动触发，支持自定义参数

**必要 Secrets**（Settings → Secrets and variables → Actions → Secrets）：

| Secret 名称 | 说明 |
|------------|------|
| `DEEPSEEK_API_KEY` | DeepSeek LLM API Key（必须至少配置一个） |
| `OPENAI_API_KEY` | OpenAI API Key（可选备用） |
| `ANTHROPIC_API_KEY` | Anthropic API Key（可选备用） |
| `AGNES_API_KEY` | Sapiens AI Agnes LLM API Key（可选备用，需配合 `LLM_PROVIDER=agnes`） |

**可选 Variables**（同路径下 Variables 标签页）：

| Variable 名称 | 默认值 | 说明 |
|--------------|--------|------|
| `TICKER_LIST` | — | 默认股票代码，逗号分隔，如 `"600519,000858"` |
| `AUTO_UNIVERSE` | `false` | 是否启用全市场自动筛选 |
| `UNIVERSE_SOURCE` | `mootdx` | 全市场数据源（akshare/mootdx/baostock） |
| `MAX_TICKERS` | — | 自动筛选后最多处理股票数 |
| `MIN_CONFIDENCE` | `55.0` | 最低 TA 置信度阈值 |
| `ALLOWED_SIGNALS` | `BUY,HOLD` | 允许的 TA 信号（支持 BUY / OVERWEIGHT / HOLD / SELL，默认只保留看涨和中性） |
| `SKIP_KRONOS` | `false` | 是否跳过 Kronos 预测 |
| `OUTPUT_LANGUAGE` | `Chinese` | 报告语言 |
| `LLM_PROVIDER` | `deepseek` | LLM 提供商（deepseek / openai / anthropic / minimax / agnes） |
| `BACKEND_URL` | — | LLM 后端 API 地址（Agnes 等供应商需要配置） |
| `ANALYSIS_TIMEOUT_MINUTES` | `30` | 分析超时时间 |
| `FEISHU_WEBHOOK_URL` | — | 飞书群机器人 Webhook URL（可选，用于推送运行结果到飞书群） |

**配置步骤：**
```bash
# 1. 将仓库推送到 GitHub
git remote add origin https://github.com/YOUR_USERNAME/trade-krono-cli.git
git push -u origin master

# 2. 在 GitHub 仓库 Settings → Secrets and variables → Actions 中配置：
#    - Secrets: DEEPSEEK_API_KEY
#    - Variables: TICKER_LIST=600519,000858

# 3. 在 Actions 面板查看运行状态
```

**手动触发示例：**
在 Actions 面板选择 `daily-analysis` → `Run workflow`，可指定：
- `tickers`: 股票代码列表
- `date`: 分析日期（留空使用昨天）
- `auto_universe`: 是否启用全市场筛选
- `skip_kronos`: 是否跳过 Kronos 预测

**运行结果：**
- 分析报告上传为 GitHub Actions artifact（保留 30 天）
- 日志和摘要在 workflow run 页面直接可见

### 持续集成 (`ci.yml`)

**触发方式：** push / PR 到 `main` 或 `master` 分支时自动运行

**测试矩阵：**
| Job | 内容 |
|-----|------|
| `lint` | ruff check + ruff format --check |
| `type-check` | mypy 类型检查 |
| `test` | pytest 全量测试 + 覆盖率 |

### 飞书通知（可选）

在 GitHub Variables 中设置 `FEISHU_WEBHOOK_URL` 后，每次 CI 或每日分析运行结束后会自动推送结果卡片到飞书群：

| 场景 | 卡片内容 |
|------|---------|
| **CI** | 分支、Commit、各 Job 状态（lint/type-check/test ✅/❌）、运行链接 |
| **每日分析** | 分析日期、股票列表、Top 3 推荐（代码/信号/综合分）、运行链接 |

飞书 Webhook URL 格式：`https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`
> 获取方式：飞书群 → 设置 → 群机器人 → 自定义机器人 → 复制 Webhook 地址

GitHub Actions 环境中无本地符号链接，workflow 会自动从 GitHub clone 外部依赖：
- `TradingAgents-astock` → `https://github.com/simonlin1212/TradingAgents-astock`
- `Kronos` → `https://github.com/shiyu-coder/Kronos`

如需 pin 特定 commit，修改 `external/repos.yaml` 中的 `commit` 字段并提交。

---

## 注意事项

1. **首次运行**：Kronos 模型需要从本地路径加载，约 1-3 分钟（GPU 模式更快）
2. **K 线数据**：使用 baostock 免费获取，每日最多约 100 只股票
3. **TA 分析**：需要配置 LLM API key（DeepSeek / OpenAI / Anthropic / MiniMax / Agnes 任一）
4. **GPU 推理**：设置 `KRONOS_DEVICE=cuda:0` 可启用 GPU 加速，需 NVIDIA 显卡 + CUDA
5. **不修改原始项目**：TradingAgents-astock 和 Kronos 仅通过 `sys.path` 注入至 `cli_anything.*` 命名空间进行调用，源代码从不被读取、写入或修改
6. **缓存**：K 线数据、TA 结果、Kronos 预测均会缓存到 SQLite，重复分析同日期股票时大幅加速；使用 `--no-cache` 可强制禁用缓存（全新分析）
7. **股票代码格式**：支持 `600519`、`sh.600519`、`SZ.000858` 等格式，自动归一化
8. **多供应商切换**：`.env` 中 `LLM_PROVIDER` 切换供应商，同时确保对应 API key 已配置
9. **不确定性量化**：默认 `sample_count=1` 时 `path_dispersion=null`，`confidence_score` 仅基于方向置信度；设置 `KRONOS_SAMPLE_COUNT>1` 可启用跨样本真实不确定性
10. **baostock 登录**：baostock 全局单例登录，使用线程锁保护；已有令牌桶限流保护
