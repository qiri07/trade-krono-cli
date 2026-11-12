#!/usr/bin/env python3
"""combined_stock_analysis 选股线代码收集模块。

包含从各选股线结果文件中提取 6 位代码集合的函数，
以及 collect_source_sets 汇总函数。
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from loguru import logger

from scripts._utils import is_bse_code, normalize_ticker_key


def _date_tags(date_str: str) -> tuple[str, str]:
    """返回 (无连字符, 有连字符) 两种日期格式。"""
    return date_str.replace("-", ""), date_str


# 各选股线结果文件名模板（date_compact 无连字符，date_dash 有连字符）
_TEMPLATES: dict[str, str] = {
    "buffett": "buffett_screen_{dc}.txt",
    "buffett_wl": "buffett_dynamic_whitelist.txt",
    "resonance": "macd_resonance_{dd}.json",
    "multi_head": "multi_head_{dc}.txt",
    "double_ma": "double_ma_golden_cross_{dd}.json",
    "macd_ma": "macd_ma_screener_{dc}.json",
}

# czsc 缠论信号结果文件名（在 outputs/czsc_signal_scan/，不在 _RESULTS）
_CZSC_TEMPLATES: dict[str, str] = {
    "czsc_bi_ma": "matched_bi_ma_{dc}.csv",
    "czsc_third_buy": "matched_third_buy_{dc}.csv",
}

# 选股线中文名（用于展示）
_SOURCE_LABELS: dict[str, str] = {
    "whitelist": "白名单/巴菲特",
    "resonance": "三线共振",
    "multi_head": "多头排列",
    "double_ma": "双均线金叉",
    "macd_ma": "MACD+均线",
    "czsc_bi_ma": "缠论-笔向上+均线多头",
    "czsc_third_buy": "缠论-三买+笔向上",
    "intraday": "盘中共振",
}

# 结果目录（collect_source_sets 的默认根；可用同名参数覆盖以支持测试隔离）
_RESULTS = Path("outputs/results")
_CZSC_DIR = Path("outputs/czsc_signal_scan")


def _codes_from_buffett_screen(path: Path) -> set[str]:
    """从巴菲特筛选 txt 提取通过股票的 6 位代码集合（排除北交所）。"""
    if not path.exists():
        return set()
    text = path.read_text(encoding="utf-8")
    codes: set[str] = set()
    in_table = False
    for line in text.splitlines():
        if "代码" in line and "名称" in line:
            in_table = True
            continue
        if not in_table:
            continue
        stripped = line.strip()
        if not stripped or "--------" in stripped:
            continue
        if "失败分布" in stripped:
            break
        m = re.match(r"^\s*(\d{6}(?:\.\w+)?)\s+(\S+)", stripped)
        if not m:
            continue
        raw = m.group(1)
        code6 = raw.split(".")[0]
        # 排除北交所（920 开头或 .BJ 后缀）
        if is_bse_code(code6) or raw.upper().endswith(".BJ"):
            continue
        codes.add(code6)
    return codes


def _codes_from_buffett_whitelist(path: Path) -> set[str]:
    """从动态白名单文件读取 6 位代码集合（排除北交所）。"""
    if not path.exists():
        return set()
    text = path.read_text(encoding="utf-8").strip()
    codes: set[str] = set()
    for c in text.split(","):
        c = c.strip()
        if not c:
            continue
        code6 = c.split(".")[0]
        if is_bse_code(code6) or c.upper().endswith(".BJ"):
            continue
        codes.add(code6)
    return codes


def _codes_from_resonance(path: Path) -> set[str]:
    """从三线共振 JSON 提取 ticker 集合。"""
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return set()
    codes: set[str] = set()
    for item in data if isinstance(data, list) else []:
        ticker = item.get("ticker", "") if isinstance(item, dict) else ""
        if ticker:
            codes.add(normalize_ticker_key(ticker).split(".")[1])
    return codes


def _codes_from_multi_head(path: Path) -> set[str]:
    """从多头排列 txt 提取全部带前缀 ticker 的 6 位代码。"""
    if not path.exists():
        return set()
    text = path.read_text(encoding="utf-8")
    found = set(re.findall(r"\b(?:sh|sz|bj|hk)\.(\d{6})", text))
    return found


def _codes_from_double_ma(path: Path) -> set[str]:
    """从双均线金叉 JSON 提取 ticker 集合。"""
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return set()
    codes: set[str] = set()
    for item in data if isinstance(data, list) else []:
        ticker = item.get("ticker", "") if isinstance(item, dict) else ""
        if ticker:
            codes.add(normalize_ticker_key(ticker).split(".")[1])
    return codes


def _codes_from_macd_ma(path: Path) -> set[str]:
    """从 MACD+均线筛选 JSON 提取 ticker 集合（格式与双均线一致）。"""
    return _codes_from_double_ma(path)


def _codes_from_czsc_csv(path: Path) -> set[str]:
    """从 czsc 信号扫描 CSV 提取 6 位代码集合。

    CSV 列：symbol, dt, close, signals, matched_signals
    symbol 为带前缀 ticker，如 sh.600519，取其末段 6 位代码。

    Args:
        path: czsc 扫描输出的 CSV 路径。

    Returns:
        6 位代码集合；文件缺失或格式异常时返回空集。
    """
    if not path.exists():
        return set()
    codes: set[str] = set()
    try:
        with open(path, encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            for row in reader:
                symbol = (row.get("symbol") or "").strip()
                if not symbol:
                    continue
                code6 = symbol.split(".")[-1].strip()
                if len(code6) != 6 or not code6.isdigit():
                    continue
                # 排除北交所（920 开头）
                if is_bse_code(code6):
                    continue
                codes.add(code6)
    except (OSError, csv.Error):
        return set()
    return codes


def collect_source_sets(
    date_str: str,
    results_dir: Path | None = None,
    czsc_dir: Path | None = None,
) -> dict[str, set[str]]:
    """汇总各选股线的 6 位代码集合。

    返回 {来源键: 6 位代码集合}。

    来源键说明：
      - whitelist: 白名单/巴菲特（三路并集后仅计 1 个来源，避免交集计数虚高）
        · 巴菲特动态白名单 buffett_dynamic_whitelist.txt
        · 当日巴菲特筛选结果 buffett_screen_{dc}.txt
        · 当日 daily_analysis 分析目录 outputs/results/<code>/
      - resonance: 三线共振（MACD DIF）macd_resonance_{dd}.json
      - multi_head: 多头排列 multi_head_{dc}.txt
      - double_ma: 双均线金叉 double_ma_golden_cross_{dd}.json
      - macd_ma: MACD+均线筛选 macd_ma_screener_{dc}.json（第10步，MA5>MA13+MACD>0
        +剔ST/涨停/小市值/PE异常）
      - czsc_bi_ma: 缠论-笔向上+均线多头
      - czsc_third_buy: 缠论-三买+笔向上
      - intraday: 盘中共振（从最新的 intraday_scan_YYYYMMDD_HHMM.json 读取）

    注意：白名单/巴菲特被合并为单一 "whitelist" 来源参与命中计数；
    其余选股线独立计数。
    """
    if results_dir is None:
        results_dir = _RESULTS
    if czsc_dir is None:
        czsc_dir = _CZSC_DIR

    dc, dd = _date_tags(date_str)
    fmaps = {k: t.format(dc=dc, dd=dd) for k, t in _TEMPLATES.items()}

    result: dict[str, set[str]] = {}

    # 白名单三路并集：动态白名单 + 当日筛选 + 当日分析目录
    whitelist_codes = _codes_from_buffett_whitelist(results_dir / fmaps["buffett_wl"])
    whitelist_codes |= _codes_from_buffett_screen(results_dir / fmaps["buffett"])
    for d in results_dir.iterdir() if results_dir.exists() else []:
        if d.is_dir() and re.fullmatch(r"\d{6}|hk\.\d{5}", d.name):
            whitelist_codes.add(normalize_ticker_key(d.name).split(".")[1])
    result["whitelist"] = whitelist_codes

    result["resonance"] = _codes_from_resonance(results_dir / fmaps["resonance"])
    result["multi_head"] = _codes_from_multi_head(results_dir / fmaps["multi_head"])
    result["double_ma"] = _codes_from_double_ma(results_dir / fmaps["double_ma"])
    result["macd_ma"] = _codes_from_macd_ma(results_dir / fmaps["macd_ma"])

    # czsc 缠论信号（两个预设，独立于其他选股线计数）
    dc, _ = _date_tags(date_str)
    result["czsc_bi_ma"] = _codes_from_czsc_csv(
        czsc_dir / _CZSC_TEMPLATES["czsc_bi_ma"].format(dc=dc)
    )
    result["czsc_third_buy"] = _codes_from_czsc_csv(
        czsc_dir / _CZSC_TEMPLATES["czsc_third_buy"].format(dc=dc)
    )

    # 防御性告警：来源文件缺失会让该线静默记 0 只，需明确提示
    for key, tmpl in fmaps.items():
        if key in ("whitelist", "buffett_wl"):
            continue  # 白名单为三路并集 / 静态文件，单路缺失不告警
        if not (results_dir / tmpl).exists():
            logger.warning(
                f"⚠️ 选股线 [{_SOURCE_LABELS.get(key, key)}] 结果文件缺失："
                f"{tmpl}（{date_str}），该线记 0 只；如非预期请检查 daily_flow.sh 是否已先跑第 10 步"
            )
    for key, tmpl in _CZSC_TEMPLATES.items():
        czsc_path = czsc_dir / tmpl.format(dc=dc)
        if not czsc_path.exists():
            logger.warning(
                f"⚠️ 缠论选股线 [{_SOURCE_LABELS.get(key, key)}] 结果文件缺失："
                f"{czsc_path.name}（{date_str}），该线记 0 只；"
                f"如非预期请检查 daily_flow.sh 是否已先跑 czsc 步骤"
            )

    # ── 盘中扫描结果（最新的 intraday_scan_YYYYMMDD_HHMM.json）──────────
    intraday_files = sorted(
        results_dir.glob("intraday_scan_*.json"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    if intraday_files:
        latest_intraday = intraday_files[0]
        try:
            intraday_data = json.loads(latest_intraday.read_text(encoding="utf-8"))
            intraday_codes: set[str] = set()
            for item in intraday_data.get("results", []):
                if isinstance(item, dict) and item.get("ticker"):
                    # 统一为 6 位裸代码，与其他选股线保持同一代码空间，
                    # 否则 sh.600519 与 600519 会被 build_intersection 当成两只股票。
                    result_ticker = normalize_ticker_key(item["ticker"]).split(".")[-1]
                    if is_bse_code(result_ticker):
                        continue
                    intraday_codes.add(result_ticker)
            result["intraday"] = intraday_codes
            logger.info(
                f'📊 盘中共振来源（{latest_intraday.name}）: {len(intraday_codes)} 只'
            )
        except (json.JSONDecodeError, OSError, KeyError) as e:
            logger.warning(f"⚠️ 盘中共振结果文件解析失败：{latest_intraday.name}，{e}")
            result["intraday"] = set()
    else:
        logger.warning(
            f"⚠️ 盘中共振选股线 [盘中共振] 结果文件缺失："
            f"outputs/results/intraday_scan_*.json（{date_str}），该线记 0 只；"
            f"如非预期请检查 daily_flow.sh 是否已先跑 intraday 步骤"
        )
        result["intraday"] = set()

    return result
