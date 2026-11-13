"""double_ma 选股 — 配置常量。

包含双均线金叉筛选所需的所有配置：均线周期、PE 阈值、ROE 阈值、
净利润阈值、API 密钥与请求头等。
"""

from __future__ import annotations

import os

# ── 均线配置 ──────────────────────────────────────────────────────────────────────
MA_FAST, MA_SLOW = 5, 13  # 双均线：MA5 > MA13

# ── PE 阈值（由 scripts._utils 提供 PE_MAX_TECH，避免各脚本各自硬编码）─────────────
PE_MIN = 0
PE_MAX = 30.0  # 将在 cli.py 中从 _utils 导入

# ── 基本面阈值 ────────────────────────────────────────────────────────────────────
ROE_THRESHOLD = 10.0  # ROE > 10%
PROFIT_THRESHOLD = 1e8  # 1亿元 = 100,000,000 元

# ── 同花顺 API 配置 ───────────────────────────────────────────────────────────────
API_KEY = (os.getenv("HITHINK_FINANCE_API_KEY", "") or os.getenv("FUYAO_API_KEY", "")).strip()
HEADERS = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
BASE = "https://fuyao.aicubes.cn"
