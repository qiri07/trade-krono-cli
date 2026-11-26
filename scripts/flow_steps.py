#!/usr/bin/env python3
"""scripts.flow_steps — 流程 step 注册表（从 flow_runner.py 拆出）。

职责：
  · 定义全部 22 个流程 step（backup/check/sync/sync_etf/metadata/universe/
    buffett/resonance/multi_head/whitelist/double_ma/macd_ma/czsc/intraday/
    sixdim/fivemust/sync_hk/metadata_hk/hk_screen/combined/export/export_obsidian）
  · 依赖 env flag（PUSH_FLAG/NO_SYNC/SKIP_HK 等）决定 step 是否默认启用
  · 依赖 expected_trading_day() 动态计算今日日期

保持与 flow_runner.py 原实现逐字一致，便于测试直接引用。
"""

from __future__ import annotations

import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts._utils import expected_trading_day  # noqa: E402

# 透传 env flag（与 daily_flow.sh 历史行为一致）
DRY_RUN = os.getenv("DRY_RUN", "0") == "1"
NO_PUSH = os.getenv("NO_PUSH", "0") == "1"
NO_SYNC = os.getenv("NO_SYNC", "0") == "1"
SKIP_ETF = os.getenv("SKIP_ETF", "0") == "1"
SKIP_HK = os.getenv("SKIP_HK", "0") == "1"
EXPORT = os.getenv("EXPORT", "0") == "1"

PUSH_FLAG = 0 if NO_PUSH else 1

DEFAULT_STEP_TIMEOUT_SECONDS = 3600

# 今日 A 股（沪+深）K 线数据最小阈值：低于此数量时，跳过所有选股步骤
MIN_TODAY_KLINE_COUNT = 5000

# 由 backup step 统一备份后，这些自带备份的 step 追加 --no-backup，保证一轮流程只备一次
BACKUP_STEP_NAME = "backup"
STEPS_WITH_AUTO_BACKUP = frozenset({"sync", "sync_etf"})


@dataclass(frozen=True)
class Step:
    """单个可执行 step 定义。

    Attributes:
        name: step 标识（如 "sync"、"buffett"），用于 `--step` 与 `--enable/--disable`
        description: 人类可读说明
        command: 执行命令（list[str]，shell=False）
        enabled_by_default: 默认是否纳入 `--all` 串行（False 时需 `--enable` 显式开启）
        depends_on: 前置 step name 列表（`--all` 时保证顺序）
    """

    name: str
    description: str
    command: list[str]
    enabled_by_default: bool = True
    depends_on: list[str] = field(default_factory=list)
    timeout: int = DEFAULT_STEP_TIMEOUT_SECONDS

    def to_command_str(self) -> str:
        return " ".join(self.command)


# ── Step 注册表（按依赖序）────────────────────────────────────────────

STEPS: list[Step] = [
    Step(
        name="backup",
        description=(
            "写入前数据备份（backup_cache.py；本轮只备一次，"
            "sync/sync_etf 将自动跳过各自备份）"
        ),
        command=["uv", "run", "python", "scripts/backup_cache.py"],
        enabled_by_default=True,
        timeout=1800,
    ),
    Step(
        name="check",
        description="数据现状检查 + 补拉计划生成（check_data_status.py）",
        command=["uv", "run", "python", "scripts/check_data_status.py"],
        enabled_by_default=True,
    ),
    Step(
        name="sync",
        description="K 线增量补拉（仅 A 股沪深，fill_missing_and_today.py --markets a）",
        command=[
            "uv",
            "run",
            "python",
            "scripts/fill_missing_and_today.py",
            "--plan",
            f"outputs/results/sync_plan_{time.strftime('%Y%m%d')}.json",
            "--markets",
            "a",
        ],
        enabled_by_default=not NO_SYNC,
        depends_on=["check", "backup"],
        timeout=7200,
    ),
    Step(
        name="sync_etf",
        description="ETF K 线同步（etf_history_sync.py，增量）——默认禁用，需 --enable sync_etf 显式开启",
        command=["uv", "run", "python", "scripts/etf_history_sync.py"],
        enabled_by_default=False,
        depends_on=["sync"],
        timeout=7200,
    ),
    Step(
        name="metadata",
        description="参考数据 TTL 刷新（fetch_reference_data.py）",
        command=["uv", "run", "python", "scripts/fetch_reference_data.py"],
        enabled_by_default=not NO_SYNC,
        depends_on=["sync"],
    ),
    Step(
        name="universe",
        description="统一前置股票池过滤（ST/北交所/ETF/次新/停牌/低价/低流动性/市值）",
        command=["uv", "run", "python", "scripts/universe_filter.py"],
        enabled_by_default=True,
        depends_on=["metadata"],
        timeout=1800,
    ),
    Step(
        name="buffett",
        description="巴菲特六闸门筛选（并行版）",
        command=["uv", "run", "python", "tests/buffett_screen_parallel.py"],
        enabled_by_default=True,
        depends_on=["universe"],
        timeout=5400,
    ),
    Step(
        name="resonance",
        description="三线共振扫描（multi_line_resonance.py）",
        command=["uv", "run", "python", "scripts/multi_line_resonance.py"],
        enabled_by_default=True,
        depends_on=["universe"],
        timeout=7200,
    ),
    Step(
        name="multi_head",
        description="多头排列扫描（multi_scan.py）",
        command=["uv", "run", "python", "scripts/multi_scan.py"],
        enabled_by_default=True,
        depends_on=["universe"],
        timeout=7200,
    ),
    Step(
        name="whitelist",
        description="白名单分析 + AI 核实 + 飞书推送（scripts.daily_analysis）",
        command=["uv", "run", "python", "-m", "scripts.daily_analysis"],
        enabled_by_default=True,
        depends_on=["universe", "buffett"],
        timeout=3600,
    ),
    Step(
        name="double_ma",
        description="双均线金叉筛选（完整模式）",
        command=["uv", "run", "python", "scripts/double_ma_golden_cross.py"],
        enabled_by_default=True,
        depends_on=["universe"],
    ),
    Step(
        name="macd_ma",
        description="MACD+均线筛选（macd_ma_screener.py）",
        command=[
            "uv",
            "run",
            "python",
            "scripts/macd_ma_screener.py",
            "--push",
            str(PUSH_FLAG),
        ],
        enabled_by_default=True,
        depends_on=["universe"],
    ),
    Step(
        name="czsc",
        description="缠论信号全市场扫描（先 czsc_convert_db.py DB→parquet 导出，再 czsc_universe_signal_scan.py 扫描+飞书推送）",
        command=[
            "bash",
            "scripts/czsc_full_scan.sh",
            expected_trading_day(),
            str(PUSH_FLAG),
        ],
        enabled_by_default=True,
        depends_on=["universe"],
        timeout=3600,
    ),
    Step(
        name="intraday",
        description="盘中技术信号扫描（intraday_scan.py，不推送）",
        command=["uv", "run", "python", "scripts/intraday_scan.py", "--push", "0"],
        enabled_by_default=True,
        depends_on=["universe"],
    ),
    Step(
        name="sixdim",
        description="六维选股（逻辑>估值>位置>趋势>股东人数>量价，six_dim_screen.py）",
        command=["uv", "run", "python", "-m", "scripts.six_dim_screen", "--push", str(PUSH_FLAG)],
        enabled_by_default=True,
        depends_on=["universe"],
        timeout=7200,
    ),
    Step(
        name="fivemust",
        description="五必筛选（历史低位/底部扎实/业绩优秀/筹码集中/日K多头突破，a_stock_screen/cli.py --fivemust）",
        command=[
            "uv",
            "run",
            "python",
            "scripts/a_stock_screen/cli.py",
            "--all",
            "--date",
            expected_trading_day(),
            "--fivemust",
            # a_stock_screen/cli.py 只支持 --no-push（无 --push N）
            *([] if PUSH_FLAG == 1 else ["--no-push"]),
        ],
        enabled_by_default=True,
        depends_on=["universe"],
        timeout=7200,
    ),
    Step(
        name="sync_hk",
        description="港股 K 线增量同步（full_hk_history_sync.py，补到最近交易日）",
        command=[
            "uv",
            "run",
            "python",
            "scripts/full_hk_history_sync.py",
            "--end",
            expected_trading_day(),
        ],
        enabled_by_default=not NO_SYNC and not SKIP_HK,
        depends_on=["check", "backup"],
        timeout=7200,
    ),
    Step(
        name="metadata_hk",
        description="港股基本面元数据刷新（fetch_hk_metadata.py，按 TTL 增量）",
        command=["uv", "run", "python", "scripts/fetch_hk_metadata.py"],
        enabled_by_default=not SKIP_HK,
        depends_on=["sync_hk"],
        timeout=7200,
    ),
    Step(
        name="hk_screen",
        description="港股每日筛选（hk_daily_screen.py，含 JSON 落盘 + 飞书推送）",
        command=[
            "uv",
            "run",
            "python",
            "scripts/hk_daily_screen.py",
            "--date",
            expected_trading_day(),
            *(["--no-push"] if PUSH_FLAG == 0 else []),
        ],
        enabled_by_default=not SKIP_HK,
        depends_on=["sync_hk", "metadata_hk"],
        timeout=1800,
    ),
    Step(
        name="combined",
        description="综合选股交集汇总（combined_stock_analysis.py，含回测 + 飞书推送）",
        command=[
            "uv",
            "run",
            "python",
            "scripts/combined_stock_analysis.py",
            "--min-sources",
            "3",
            "--a-stock-screen",
            "1",
            "--push",
            str(PUSH_FLAG),
            "--per-stock-push",
            "1",
            "--backtest",
            "1",
            "--backtest-days",
            "90",
            "--backtest-horizon",
            "5",
        ],
        enabled_by_default=True,
        depends_on=[
            "sync",
            "buffett",
            "resonance",
            "multi_head",
            "whitelist",
            "double_ma",
            "macd_ma",
            "czsc",
            "intraday",
            "sixdim",
            "fivemust",
        ],
        timeout=10800,
    ),
    Step(
        name="export",
        description="共享数据导出（post_sync_export.sh）——默认禁用，需 EXPORT=1 或 --enable export",
        command=["bash", "scripts/post_sync_export.sh"],
        enabled_by_default=EXPORT,
        depends_on=["sync"],
        timeout=3600,
    ),
    Step(
        name="export_obsidian",
        description="每日结果导出到 Obsidian 知识库（export_obsidian_results.py）",
        command=["uv", "run", "python", "scripts/export_obsidian_results.py"],
        enabled_by_default=True,
        depends_on=["combined"],
        timeout=600,
    ),
]


def get_step(name: str) -> Step | None:
    """按 name 查找 step；未注册返回 None。"""
    for s in STEPS:
        if s.name == name:
            return s
    return None
