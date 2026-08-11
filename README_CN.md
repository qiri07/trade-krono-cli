# trade-krono-cli

> A股投研 + Kronos 预测一体化流水线 — 并行分析 N 只股票

## 快速开始

### 安装
```bash
cd trade-krono-cli
pip install -e .
```

### 配置 .env
```bash
DEEPSEEK_API_KEY=sk-xxx
KRONOS_MODEL=kronos-base
KRONOS_DEVICE=cpu
```

### 一键运行
```bash
trade-krono-cli run --tickers "600519,000858,600036" --date 2026-08-11
```

## 命令

| 命令 | 说明 |
|------|------|
| `run` | 一键并行运行 TA + Kronos |
| `ta` | 仅 TradingAgents 分析 |
| `kronos` | 仅 Kronos 预测 |
| `status` | 查看系统状态 |
| `clear-cache` | 清除缓存 |

## 详细文档

见 [README.md](README.md)
