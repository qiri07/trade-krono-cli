#!/usr/bin/env python3
"""daily_analysis 白名单解析模块。

静态白名单（Settings）+ 巴菲特动态白名单合并、北交所/ST 过滤、缓存库路径。
"""

from __future__ import annotations

from pathlib import Path

from loguru import logger

from scripts._utils import ST_NAME_KEYWORDS_MINIMAL, is_bse_or_hk
from scripts.daily_analysis.names import _get_stock_name
from trade_krono_cli.config import get_settings  # type: ignore[import-not-found]


def _get_cache_db() -> Path:
    """获取缓存数据库路径，优先从 settings 读取以支持测试隔离。"""
    # 注意：使用局部变量而非模块级全局，避免测试间污染
    try:
        settings = get_settings()
        return settings.cache_dir / "pipeline_cache.db"
    except Exception as e:
        logger.warning(f"⚠️  获取缓存路径失败: {e}，使用默认路径")
        return Path("outputs/cache/pipeline_cache.db")


def _get_whitelist() -> str:
    """从配置获取每日分析白名单（支持环境变量覆盖）。"""
    try:
        settings = get_settings()
        return settings.daily_analysis_whitelist.strip()
    except Exception as e:
        # 配置加载失败时回退到默认值
        logger.warning(f"⚠️  读取配置失败，使用默认白名单: {e}")
        return "000001,002027,601668,000932,601061,hk.00700,hk.09988,hk.00941,hk.00390,hk.01448,hk.09992"


_DYNAMIC_WHITELIST_PATH = Path("outputs/results/buffett_dynamic_whitelist.txt")


def _get_dynamic_whitelist() -> list[str]:
    """读取巴菲特动态白名单文件（逗号分隔的6位代码）。"""
    if not _DYNAMIC_WHITELIST_PATH.exists():
        return []
    try:
        content = _DYNAMIC_WHITELIST_PATH.read_text(encoding="utf-8").strip()
        if not content:
            return []
        # 排除北交所股票（920 开头的裸代码或 bj. 前缀）
        return [c.strip() for c in content.split(",") if c.strip() and not is_bse_or_hk(c.strip())]
    except Exception as e:
        logger.warning(f"⚠️  读取动态白名单失败: {e}")
        return []


def _get_merged_whitelist() -> str:
    """合并静态白名单 + 动态巴菲特白名单（去重，剔除北交所股票）。"""
    static = _get_whitelist()
    static_codes = [c.strip() for c in static.split(",") if c.strip()]
    dynamic = _get_dynamic_whitelist()
    seen: set[str] = set()
    merged: list[str] = []
    for code in static_codes + dynamic:
        code = code.strip()
        if not code or code in seen:
            continue
        # 剔除北交所股票（bj./hk. 前缀或 920 开头裸代码）
        if is_bse_or_hk(code):
            logger.debug(f"  跳过北交所股票: {code}")
            continue
        # hk. 前缀的港股正常保留
        seen.add(code)
        merged.append(code)
    return ",".join(merged)


def _filter_bse_st(tickers: list[str]) -> list[str]:
    """过滤北交所和 ST 股票，返回保留的 ticker 列表。

    北交所：代码以 920 开头或 .BJ 后缀。
    ST：名称含 ST/*ST/退市 关键词（通过 _get_stock_name 查询）。
    """
    kept: list[str] = []
    bse_skipped: list[str] = []
    st_skipped: list[str] = []
    for t in tickers:
        if is_bse_or_hk(t):
            bse_skipped.append(t)
            logger.debug(f"  跳过北交所股票: {t}")
            continue
        name = _get_stock_name(t)
        if any(kw in name for kw in ST_NAME_KEYWORDS_MINIMAL):
            st_skipped.append(t)
            logger.info(f"  剔除 ST 股: {t} {name}")
            continue
        kept.append(t)
    if bse_skipped:
        logger.info(f"  共跳过 {len(bse_skipped)} 只北交所股票: {bse_skipped}")
    if st_skipped:
        logger.info(f"  共剔除 {len(st_skipped)} 只 ST 股: {st_skipped}")
    return kept
