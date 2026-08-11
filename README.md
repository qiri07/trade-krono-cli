# trade-krono-cli

> A股投研 + Kronos 预测一体化流水线 — 并行分析 N 只股票

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

## 概述

`trade-krono-cli` 是一个命令行工具，接受 N 个 A 股股票代码，**同步并行**调用：

1. **TradingAgents-astock** — 多 Agent 深度分析（市场/情绪/基本面/政策/资金/风险辩论）
2. **Kronos** — K 线序列预测（基于深度学习的未来价格走势预测）

两者完成后自动合并，输出综合排名报告。

## 目录

- [快速开始](#快速开始)
- [安装](#安装)
- [配置说明](#配置说明)
- [使用指南](#使用指南)
- [输出说明](#输出说明)
- [架构设计](#架构设计)
- [综合打分公式](#综合打分公式)
- [依赖](#依赖)
- [测试](#测试)
- [注意事项](#注意事项)

---

## 快速开始

```bash
# 安装
pip install -e .

# 配置（复制并编辑 .env）
cp .env.example .env

# 一键运行（TA + Kronos 并行）
trade-krono-cli run --tickers "600519,000858,600036" --date 2026-08-11
```

## 安装

### 方式一：pip 安装（推荐）

```bash
cd trade-krono-cli
pip install -e .
```

如需开发依赖（测试等）：

```bash
pip install -e ".[dev]"
```

### 方式二：一键安装脚本

```bash
bash scripts/install.sh
```

安装脚本会自动：
- 检查 Python 版本（≥ 3.10）
- 安装依赖
- 创建 `outputs/` 目录
- 若缺少 `.env`，生成 `.env.example` 模板

### 环境要求

| 依赖 | 最低版本 | 说明 |
|------|----------|------|
| Python | 3.10+ | 使用 `walrus` 运算符、`typing` 扩展 |
| PyTorch | 2.0+ | Kronos 模型推理 |
| Typer | 0.9+ | CLI 框架 |
| Rich | 13+ | 终端美化输出 |
| Python-dotenv | 1.0+ | `.env` 文件加载 |

## 配置说明

### `.env` 文件

在项目根目录创建 `.env` 文件，所有配置均通过环境变量覆盖，无需修改代码。

```bash
# ── LLM API Key（至少配置一个）────────────────────────────
DEEPSEEK_API_KEY=sk-xxx
# OPENAI_API_KEY=sk-xxx
# ANTHROPIC_API_KEY=sk-ant-xxx
# MINIMAX_API_KEY=xxx
# AGNES_API_KEY=xxx

# ── LLM 行为配置 ────────────────────────────────────────
LLM_PROVIDER=deepseek          # 默认 LLM 供应商
DEEP_THINK_LLM=deepseek-chat   # 深度思考模型
QUICK_THINK_LLM=deepseek-chat  # 快速思考模型
BACKEND_URL=https://apihub.agnes-ai.cn/v1  # 后端 API 地址（可选）
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
KRONOS_SAMPLE_COUNT=1          # 采样次数
KRONOS_T=1.0                   # 采样温度
KRONOS_TOP_P=0.9               # nucleus sampling 阈值

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
| `LLM_PROVIDER` | `deepseek` | 默认供应商：`deepseek` / `openai` / `anthropic` / `minimax` / `agnes` |
| `DEEP_THINK_LLM` | `deepseek-chat` | 深度分析 Agent 使用的模型 |
| `QUICK_THINK_LLM` | `deepseek-chat` | 快速分析 Agent 使用的模型 |
| `BACKEND_URL` | — | LLM 后端 API 地址，部分供应商需要配置 |
| `MAX_DEBATE_ROUNDS` | `1` | 多空辩论最大轮次，0 表示不辩论 |
| `MAX_RISK_DISCUSS_ROUNDS` | `1` | 风险分析最大轮次 |
| `CHECKPOINT_ENABLED` | `true` | 启用后跳过已缓存的 TA 分析结果 |
| `OUTPUT_LANGUAGE` | `Chinese` | 报告语言：`Chinese` / `English` |

#### Kronos 配置

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `KRONOS_MODEL` | `kronos-base` | 模型名称，需与本地路径一致 |
| `KRONOS_TOKENIZER` | `kronos-Tokenizer-base` | Tokenizer 名称 |
| `KRONOS_DEVICE` | `cpu` | 推理设备，`cpu` 或 `cuda:0` |
| `KRONOS_LOOKBACK` | `400` | 用于预测的历史 K 线根数 |
| `KRONOS_PRED_LEN` | `30` | 预测未来多少根 K 线 |
| `KRONOS_SAMPLE_COUNT` | `1` | 采样次数，>1 时取均值 |
| `KRONOS_T` | `1.0` | 采样温度，越高越随机 |
| `KRONOS_TOP_P` | `0.9` | Nucleus sampling 阈值 |

#### 过滤配置

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MIN_CONFIDENCE` | `55.0` | TA 置信度低于此值的股票不参与综合排名 |
| `ALLOWED_SIGNALS` | `BUY,HOLD` | 只保留信号在此列表中的股票 |

#### 路径配置

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `TRADINGAGENTS_ROOT` | `/run/media/onai/MyDisk/Work/TradingAgents-astock` | TradingAgents-astock 项目根目录 |
| `KRONOS_ROOT` | `/run/media/onai/MyDisk/Work/Kronos` | Kronos 项目根目录 |

> **注意**：路径配置使用绝对路径，若项目位置不同请修改 `TRADINGAGENTS_ROOT` 和 `KRONOS_ROOT`。

### 通过环境变量覆盖

`.env` 中的配置也可以通过命令行环境变量覆盖：

```bash
export LLM_PROVIDER=openai
export KRONOS_DEVICE=cuda:0
trade-krono-cli run --tickers "600519" --date 2026-08-11
```

## 使用指南

### 命令总览

```
trade-krono-cli run        # 一键运行 TA + Kronos 并行流水线
trade-krono-cli ta         # 仅 TradingAgents 选股分析
trade-krono-cli kronos     # 仅 Kronos 批量预测
trade-krono-cli status     # 查看系统状态（密钥、缓存、模型）
trade-krono-cli clear-cache # 清除所有缓存
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
```

**`run` 参数：**

| 参数 | 默认值 | 说明 |
|------|--------|------|
| `--tickers, -t` | — | 逗号分隔的股票代码（二选一） |
| `--config, -c` | — | 股票列表文件路径（二选一） |
| `--date, -d` | — | 分析日期 `YYYY-MM-DD`（必填） |
| `--min-confidence` | `55.0` | 最低 TA 置信度 |
| `--signals` | `BUY,HOLD` | 允许的 TA 信号 |
| `--skip-kronos` | `false` | 跳过 Kronos 预测 |
| `--pred-len` | `30` | Kronos 预测步长 |
| `--lookback` | `400` | Kronos 历史回看长度 |
| `--json` | `outputs/results.json` | JSON 报告输出路径 |
| `--html` | `outputs/report.html` | HTML 报告输出路径 |
| `--no-cache` | `false` | 禁用缓存 |

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

## 输出说明

### 控制台输出

```
🚀 启动流水线 3 只 → 2026-08-11
  TA分析 [0/2]
  TA分析 [1/2]
  TA分析 [2/2]
  并行执行 [1/2]
  并行执行 [2/2]
┌──────────┬────────┬────────┬──────────┬───────────┬────────────┬──────────┐
│   排名   │  代码  │ TA信号 │  置信度  │ Kronos方向│ 预期涨幅%  │  综合分  │
├──────────┼────────┼────────┼──────────┼───────────┼────────────┼──────────┤
│    1     │600519  │  BUY   │   80.0   │    UP     │    3.20    │  82.10   │
│    2     │000858  │  HOLD  │   60.0   │   DOWN    │   -1.50    │  45.00   │
└──────────┴────────┴────────┴──────────┴───────────┴────────────┴──────────┘
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
    "composite_score": 82.1
  }
]
```

### HTML 报告

路径：`outputs/report.html`（由 `--html` 参数指定），自动生成美观的 HTML 表格。

### 日志

- 流水线日志：`outputs/pipeline.log`
- 内存日志：`outputs/memory_log.jsonl`（每次运行的性能指标）

## 架构设计

```
trade-krono-cli
├── trade_krono_cli/
│   ├── cli.py          # Typer CLI 入口
│   ├── config.py       # 配置管理（.env → Settings 单例）
│   ├── data.py         # K 线获取（baostock）
│   ├── security.py     # 密钥校验 + 输入校验 + 重试 + 限流
│   ├── cache.py        # SQLite 缓存层
│   ├── logger.py       # 日志配置
│   ├── ta_runner.py    # TradingAgents 封装
│   ├── kronos_runner.py # Kronos 预测封装
│   ├── merge.py        # 结果合并 + 综合打分
│   ├── report.py       # JSON/HTML/控制台报告
│   └── pipeline.py     # 并行流水线编排
├── scripts/
│   └── install.sh      # 一键安装脚本
├── tests/              # 测试套件
├── outputs/            # 运行时输出（.gitignore 忽略）
└── pyproject.toml      # 项目配置
```

### 并行策略

`pipeline.py` 使用 `concurrent.futures.ThreadPoolExecutor` 实现：
- TA 分析并行：每只股票独立线程，共享 LLM API 调用
- Kronos 预测并行：每只股票独立线程，共享 GPU 推理（`cuda:0` 模式下有竞争，建议串行或用 CPU）
- TA 与 Kronos 异步执行：TA 完成后等待 Kronos 全部完成，再合并

## 综合打分公式

```
score = TA_confidence × 0.4 + Kronos_change_map × 0.4 + direction_bonus × 0.2

其中：
- TA_confidence: 0–100，直接映射
- Kronos_change_map: [-50%, +50%] → [0, 100]（线性映射）
- direction_bonus: UP=+20, FLAT=0, DOWN=-20
```

综合得分最高者排名最前。

## 依赖

### Python 依赖（pyproject.toml）

| 包 | 用途 |
|----|------|
| `typer` | CLI 框架 |
| `rich` | 终端美化输出 |
| `loguru` | 日志 |
| `python-dotenv` | `.env` 加载 |
| `pandas` + `baostock` | A 股数据获取 |
| `torch` | Kronos 模型推理 |

### 外部项目（只读调用，不修改源码）

| 项目 | 路径 | 用途 |
|------|------|------|
| `TradingAgents-astock` | `TRADINGAGENTS_ROOT` | TA 多 Agent 分析 |
| `Kronos` | `KRONOS_ROOT` | K 线序列预测 |

通过 `sys.path` 注入方式调用，不修改原始项目代码。

## 测试

```bash
pytest tests/ -v
pytest tests/ -v --cov=trade_krono_cli --cov-report=html
```

测试文件：

| 文件 | 覆盖模块 |
|------|----------|
| `test_cli.py` | CLI 入口 |
| `test_data.py` | K 线数据获取 |
| `test_merge.py` | 结果合并逻辑 |
| `test_pipeline.py` | 流水线编排 |
| `test_report.py` | 报告生成 |
| `test_security.py` | 密钥校验、输入校验、重试 |

## 注意事项

1. **首次运行**：Kronos 模型需要从本地路径加载，约 1–3 分钟（GPU 模式更快）
2. **K 线数据**：使用 baostock 免费获取，每日最多约 100 只股票
3. **TA 分析**：需要配置 LLM API key（DeepSeek / OpenAI / Anthropic / MiniMax / Agnes 任一）
4. **GPU 推理**：设置 `KRONOS_DEVICE=cuda:0` 可启用 GPU 加速，需 NVIDIA 显卡 + CUDA
5. **不修改原始项目**：通过 `sys.path` 注入方式调用，不修改 TradingAgents-astock 和 Kronos 代码
6. **缓存**：K 线数据、TA 结果、Kronos 预测均会缓存到 SQLite，重复分析同日期股票时大幅加速
7. **股票代码格式**：支持 `600519`、`sh.600519`、`SZ.000858` 等格式，自动归一化
8. **多供应商切换**：`.env` 中 `LLM_PROVIDER` 切换供应商，同时确保对应 API key 已配置

## License

MIT
