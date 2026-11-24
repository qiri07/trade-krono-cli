#!/usr/bin/env python3
"""
每日投研流程结果文件导出到 Obsidian 知识库

将 outputs/results/ 下的核心结果文件复制到 /run/media/onai/MyDisk/Work/obsidian/00-Stock/00-Stock/
按日期组织，便于知识管理和回顾。
"""

from __future__ import annotations

import argparse
import shutil
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts._utils import expected_trading_day  # noqa: E402

# 目标 Obsidian 仓库路径
OBSIDIAN_VAULT = Path("/run/media/onai/MyDisk/Work/obsidian/00-Stock/00-Stock")
RESULTS_DIR = PROJECT_ROOT / "outputs" / "results"

# 需要复制的文件模式（按优先级）
FILE_PATTERNS = [
    # 核心汇总类（最高优先级）
    "combined_report_*.txt",
    "combined_intersection_*.json",
    "cross_flow_sixdim_*.txt",
    # 选股线结果
    "buffett_screen_*.txt",
    "buffett_screen_*_ai.json",
    "macd_resonance_*.json",
    "macd_resonance_*_ai.json",
    "multi_head_*.txt",
    "double_ma_golden_cross_*.json",
    "double_ma_golden_cross_pe_macd_*.json",
    "double_ma_golden_cross_pe_macd_roe_*.json",
    "macd_ma_screener_*.json",
    "six_dim_screen_*.json",
    "six_dim_screen_*.md",
    # 盘中扫描
    "intraday_scan_*.json",
    # 前置股票池
    "universe_*.json",
    # 流程日志（可选，按需开启）
    # "daily_flow_*.log",
    # "flow_checkpoint_*.json",
]


def get_today_date_str() -> str:
    """返回今天的日期字符串（YYYYMMDD）"""
    return expected_trading_day().replace("-", "")


def find_latest_files_for_date(date_str: str) -> list[Path]:
    """查找指定日期的最新结果文件"""
    # 支持两种日期格式：YYYYMMDD 和 YYYY-MM-DD
    date_str_dashed = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}"
    files = []
    seen = set()
    for pattern in FILE_PATTERNS:
        # 将日期注入模式匹配
        matches = list(RESULTS_DIR.glob(pattern))
        for f in matches:
            # 过滤：只取当天的文件（文件名中包含日期，支持两种格式）
            if date_str in f.name or date_str_dashed in f.name:
                if f not in seen:
                    files.append(f)
                    seen.add(f)
    return files


def copy_to_obsidian(date_str: str, files: list[Path], dry_run: bool = False) -> int:
    """复制文件到 Obsidian 仓库，按日期创建子目录"""
    target_dir = OBSIDIAN_VAULT / f"daily_{date_str}"
    if not dry_run:
        target_dir.mkdir(parents=True, exist_ok=True)

    copied = 0
    skipped = 0
    for src in files:
        dst = target_dir / src.name
        try:
            if dry_run:
                print(f"  [DRY-RUN] {src.name} -> {dst.relative_to(OBSIDIAN_VAULT)}")
            else:
                shutil.copy2(src, dst)
                print(f"  ✅ {src.name} -> {dst.relative_to(OBSIDIAN_VAULT)}")
            copied += 1
        except Exception as e:
            print(f"  ❌ {src.name}: {e}")
            skipped += 1

    if not dry_run and copied > 0:
        # 创建/更新索引文件
        index_path = target_dir / "README.md"
        create_index_md(index_path, date_str, files)
        print(f"  📄 索引文件: {index_path.relative_to(OBSIDIAN_VAULT)}")

    print(f"\n📊 完成: 复制 {copied} 个文件, 跳过 {skipped} 个")
    print(f"📁 目标目录: {target_dir}")
    return 0 if skipped == 0 else 1


def create_index_md(index_path: Path, date_str: str, files: list[Path]) -> None:
    """生成每日结果索引 Markdown"""
    # 按类别分组
    categories: dict[str, list[str]] = {
        "📋 核心汇总": [],
        "🎯 选股线结果": [],
        "📈 盘中扫描": [],
        "🔍 前置股票池": [],
        "📝 流程日志": [],
    }

    for f in files:
        name = f.name
        if name.startswith("combined_report") or name.startswith("cross_flow_sixdim") or name.startswith("combined_intersection"):
            categories["📋 核心汇总"].append(name)
        elif name.startswith("buffett_screen") or name.startswith("macd_resonance") or name.startswith("multi_head") or name.startswith("double_ma") or name.startswith("macd_ma_screener") or name.startswith("six_dim_screen"):
            categories["🎯 选股线结果"].append(name)
        elif name.startswith("intraday_scan"):
            categories["📈 盘中扫描"].append(name)
        elif name.startswith("universe"):
            categories["🔍 前置股票池"].append(name)
        elif name.startswith("daily_flow") or name.startswith("flow_checkpoint"):
            categories["📝 流程日志"].append(name)

    lines = [
        f"# 每日投研结果索引 - {date_str[:4]}-{date_str[4:6]}-{date_str[6:]}",
        "",
        f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        "来源: `trade-krono-cli/outputs/results/`",
        "",
        "---",
        "",
    ]

    for cat, items in categories.items():
        if items:
            lines.append(f"## {cat}")
            lines.append("")
            for item in sorted(items):
                lines.append(f"- [[{item}]]")
            lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("*本索引由 `export_obsidian_results.py` 自动生成*")

    index_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="导出每日投研结果到 Obsidian")
    parser.add_argument("--date", default=None, help="指定日期 (YYYYMMDD)，默认今日")
    parser.add_argument("--dry-run", action="store_true", help="仅打印不复制")
    parser.add_argument("--all-dates", action="store_true", help="导出所有有结果的日期")
    args = parser.parse_args()

    if not OBSIDIAN_VAULT.exists():
        print(f"❌ Obsidian 仓库不存在: {OBSIDIAN_VAULT}")
        return 1

    if not RESULTS_DIR.exists():
        print(f"❌ 结果目录不存在: {RESULTS_DIR}")
        return 1

    if args.all_dates:
        # 扫描所有日期
        all_files = list(RESULTS_DIR.glob("*"))
        dates = set()
        for f in all_files:
            # 从文件名提取日期
            import re
            m = re.search(r'(\d{8})', f.name)
            if m:
                dates.add(m.group(1))
        for date_str in sorted(dates):
            print(f"\n{'='*60}")
            print(f"处理日期: {date_str}")
            files = find_latest_files_for_date(date_str)
            if files:
                copy_to_obsidian(date_str, files, args.dry_run)
            else:
                print("  无匹配文件")
        return 0

    date_str = args.date or get_today_date_str()
    print(f"📅 处理日期: {date_str}")
    print(f"📂 源目录: {RESULTS_DIR}")
    print(f"📂 目标目录: {OBSIDIAN_VAULT}/daily_{date_str}")

    files = find_latest_files_for_date(date_str)
    if not files:
        print("⚠️  未找到该日期的结果文件")
        return 0

    print(f"🔍 找到 {len(files)} 个匹配文件:")
    for f in sorted(files):
        print(f"  - {f.name}")

    return copy_to_obsidian(date_str, files, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
