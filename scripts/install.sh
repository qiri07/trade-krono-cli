#!/bin/bash
# trade-krono-cli 一键安装脚本
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "🔧 安装 trade-krono-cli..."

# 检查 Python 版本
python3 --version || { echo "❌ 需要 Python 3.10+"; exit 1; }

# 安装依赖
echo "📦 安装依赖..."
pip install -e ".[dev]"

# 创建输出目录
mkdir -p outputs

# 检查 .env
if [ ! -f .env ]; then
    echo "⚠️  未检测到 .env 文件，请复制 .env.example 并配置"
    cat > .env.example << 'EOF'
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
EOF
    echo "✅ 已创建 .env.example，请编辑后重命名为 .env"
fi

echo ""
echo "✅ 安装完成！"
echo ""
echo "💡 使用示例:"
echo "   trade-krono-cli run --tickers '600519,000858' --date 2026-08-11"
echo "   trade-krono-cli status"
echo ""
