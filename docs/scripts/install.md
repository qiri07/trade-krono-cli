# install.sh — 一键安装脚本

## 作用

`trade-krono-cli` 的初始安装脚本，完成：
1. 检查 Python 版本（需 3.10+）
2. 安装依赖（`pip install -e ".[dev]"`，含 dev 依赖）
3. 创建 `outputs/` 输出目录
4. 检测 `.env`，缺失时生成 `.env.example` 模板并提示

## 用法

```bash
bash scripts/install.sh
```

## 步骤详解

| 步骤 | 动作 | 失败处理 |
|------|------|----------|
| 1 | `python3 --version` 检查 | < 3.10 报错退出 |
| 2 | `pip install -e ".[dev]"` | 中断（`set -e`） |
| 3 | `mkdir -p outputs` | — |
| 4 | 检测 `.env`，缺失则写 `.env.example` | 提示手动配置 |

## 生成的 `.env.example` 模板

```bash
# LLM API Key（至少配置一个）
DEEPSEEK_API_KEY=sk-xxx
# OPENAI_API_KEY=sk-xxx
# ANTHROPIC_API_KEY=sk-ant-xxx

# Kronos 配置
KRONOS_MODEL=kronos-base
KRONOS_DEVICE=cpu
KRONOS_LOOKBACK=400
KRONOS_PRED_LEN=30

# TradingAgents 配置
LLM_PROVIDER=deepseek
MAX_DEBATE_ROUNDS=1
```

> 模板只是最小骨架。完整项目实际使用的 `.env` 还包含 `HITHINK_FINANCE_API_KEY`、`DATA_PROVIDER`、`DATA_FALLBACK`、Agnes LLM 限流配置等字段（见 `.env.example` 项目内完整版与 AGENTS.md「密钥安全」节）。

## 安装后

```bash
# 复制并配置 .env
cp .env.example .env
vim .env    # 填入真实 API Key

# 验证安装
uv run trade-krono-cli status
```

## 注意事项

- 本脚本使用 `pip` 而非 `uv`。项目宪法（AGENTS.md）规定日常依赖管理用 `uv`（`uv sync` / `uv add`），`install.sh` 是面向「全新机器首次安装」的引导脚本，历史原因用 pip。新项目建议直接用 `uv sync` 替代。
- `pip install -e ".[dev]"` 会安装开发依赖（pytest / ruff / mypy 等）。
- `set -e` 使任一步失败立即退出。
