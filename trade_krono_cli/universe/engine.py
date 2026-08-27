"""
Universe Engine — 多阶段 A 股市场范围发现与过滤。

编排流程：
  Provider.get_universe()           → ~5300 A 股
      ↓
  StaticFilterStage                 → 排除 ST/停牌/次新   ~4500
      ↓
  FundamentalFilterStage            → 排除 PE/PB/市值异常  ~2000
      ↓
  FactorFilterStage                 → 排除低流动性        ~500
      ↓
  list[str]                         → 送入 TA / Kronos
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from loguru import logger

if TYPE_CHECKING:
    from trade_krono_cli.configs.filters import FilterConfig

from trade_krono_cli.universe.provider import (
    UniverseProvider,
    UniverseTicket,
    get_universe_provider,
)
from trade_krono_cli.universe.stages.static import StaticFilterStage
from trade_krono_cli.universe.stages.fundamental import FundamentalFilterStage
from trade_krono_cli.universe.stages.factor import FactorFilterStage
from trade_krono_cli.universe.stages.rules import FilterRulesStage


# ── 缓存路径 ──────────────────────────────────────────────────────────────────

CACHE_DIR = Path(__file__).resolve().parent.parent.parent / "outputs" / "cache" / "universe"


# ── UniverseEngine ────────────────────────────────────────────────────────────

class UniverseEngine:
    """
    A 股市场范围发现引擎。

    通过多阶段管道从全市场 A 股中逐层过滤，最终产出让 TA/Kronos 消费的股票列表。

    用法：
        engine = UniverseEngine.from_config(filter_config)
        tickers = engine.run(eval_date="2026-08-13")
    """

    def __init__(
        self,
        provider: UniverseProvider,
        stages: list,  # list[FilterStage]
        cache_dir: Optional[Path] = None,
        cache_ttl_hours: int = 4,
    ):
        self._provider = provider
        self._stages = stages
        self._cache_dir = cache_dir or CACHE_DIR
        self._cache_ttl_hours = cache_ttl_hours

    # ── 工厂方法 ─────────────────────────────────────────────────────────────

    @classmethod
    def from_config(
        cls,
        filter_config: "FilterConfig",
        universe_source: str = "akshare",
        cache_dir: Optional[Path] = None,
        cache_ttl_hours: int = 4,
    ) -> "UniverseEngine":
        """
        从 FilterConfig 构建 UniverseEngine。

        Parameters
        ----------
        filter_config : FilterConfig
            包含 universe_source, market_cap_range, pe_range 等字段
        universe_source : str
            数据源名称，默认 "akshare"
        cache_dir : Path, optional
            缓存目录
        cache_ttl_hours : int
            缓存有效期（小时）
        """
        provider = get_universe_provider(universe_source)
        if provider is None:
            provider = get_universe_provider("akshare")

        stages: list = []

        # Stage 1: 静态过滤（ST / 停牌 / 次新）
        from trade_krono_cli.configs.abnormality import AbnormalityConfig
        abnormality = AbnormalityConfig()
        # 从 filter_config 推断异常配置（兼容旧字段）
        stages.append(StaticFilterStage(
            exclude_st=filter_config.exclude_st,
            skip_suspended=True,
            skip_new_stock=abnormality.skip_new_stock,
            new_stock_min_days=abnormality.new_stock_min_days,
            exclude_low_price=filter_config.exclude_low_price,
            low_price_threshold=filter_config.low_price_threshold,
        ))

        # Stage 2: 基本面过滤
        stages.append(FundamentalFilterStage(
            market_cap_range=filter_config.market_cap_range,
            pe_range=filter_config.pe_range,
            pb_range=filter_config.pb_range,
            min_pb=filter_config.min_pb,
            industry_whitelist=filter_config.industry_whitelist,
            industry_blacklist=filter_config.industry_blacklist,
        ))

        # Stage 2.5: 自定义规则过滤（filter_rules）
        if filter_config.filter_rules:
            stages.append(FilterRulesStage(rules=filter_config.filter_rules))

        # Stage 3: 因子过滤（流动性）
        stages.append(FactorFilterStage(
            min_volume_ratio=filter_config.min_volume_ratio,
            min_turnover_rate=filter_config.min_turnover_rate,
        ))

        return cls(
            provider=provider,
            stages=stages,
            cache_dir=cache_dir,
            cache_ttl_hours=cache_ttl_hours,
        )

    # ── 主入口 ───────────────────────────────────────────────────────────────

    def run(self, eval_date: str = "") -> list[str]:
        """
        执行完整市场范围发现流程。

        Parameters
        ----------
        eval_date : str
            评估日期（YYYY-MM-DD），用于缓存键生成；
            空字符串表示使用当前日期。

        Returns
        -------
        list[str]
            通过所有阶段过滤的股票代码列表（归一化格式）
        """
        date_key = eval_date or _today_str()
        cache_key = self._cache_key(date_key)

        # 尝试缓存
        cached = self._load_cache(cache_key, date_key)
        if cached is not None:
            return cached

        # 执行管道
        tickets = self._provider.get_universe()
        if not tickets:
            logger.warning("UniverseProvider 返回空列表")
            return []

        logger.info(f"🚀 UniverseEngine: 初始市场 {len(tickets)} 只 A 股")

        for stage in self._stages:
            tickets = stage.filter(tickets)
            if not tickets:
                logger.warning(f"Stage {stage.name} 过滤后为空，终止")
                break

        tickers = [t.ticker for t in tickets]

        # 写入缓存
        self._save_cache(cache_key, tickers, date_key)

        logger.info(
            f"✅ UniverseEngine 完成: {len(tickets)} 只股票进入候选池"
        )
        return tickers

    # ── 缓存 ─────────────────────────────────────────────────────────────────

    def _cache_key(self, date_key: str) -> str:
        """基于 provider name + stages 配置 + 日期生成缓存键。"""
        parts = [
            self._provider.name,
            str([s.name for s in self._stages]),
            date_key,
        ]
        raw = "|".join(parts)
        return hashlib.md5(raw.encode()).hexdigest()[:16]

    def _cache_path(self, key: str) -> Path:
        return self._cache_dir / f"{key}.json"

    def _load_cache(self, key: str, date_key: str) -> Optional[list[str]]:
        path = self._cache_path(key)
        if not path.exists():
            return None
        try:
            age_hours = (time.time() - path.stat().st_mtime) / 3600
            if age_hours > self._cache_ttl_hours:
                logger.debug(f"Universe cache expired ({age_hours:.1f}h)")
                return None
            data = json.loads(path.read_text(encoding="utf-8"))
            logger.info(f"📦 Universe cache hit: {len(data)} tickers (age={age_hours:.1f}h)")
            return data
        except Exception as e:
            logger.debug(f"Universe cache load failed: {e}")
            return None

    def _save_cache(self, key: str, tickers: list[str], date_key: str) -> None:
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(
                {"date": date_key, "count": len(tickers), "tickers": tickers},
                ensure_ascii=False,
            )
            self._cache_path(key).write_text(payload, encoding="utf-8")
        except Exception as e:
            logger.debug(f"Universe cache save failed: {e}")

    # ── 内省 ─────────────────────────────────────────────────────────────────

    def stage_summary(self) -> list[dict]:
        """返回各阶段的描述信息。"""
        return [
            {"name": s.name, "class": s.__class__.__name__}
            for s in self._stages
        ]


# ── 便捷函数 ──────────────────────────────────────────────────────────────────

def get_universe(
    filter_config: "FilterConfig",
    universe_source: str = "akshare",
    eval_date: str = "",
) -> list[str]:
    """
    便捷函数：从 FilterConfig 直接获取宇宙列表。

    等价于 UniverseEngine.from_config(...) 的速写。
    """
    engine = UniverseEngine.from_config(filter_config, universe_source=universe_source)
    return engine.run(eval_date=eval_date)


def _today_str() -> str:
    from datetime import date
    return date.today().strftime("%Y-%m-%d")
