"""共享测试辅助函数。"""

from __future__ import annotations

import os
import re
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from trade_krono_cli.data_providers.base import KlineData, RealtimeQuote, StockMetadata
from trade_krono_cli.data_providers.factory import reset_data_factory


def _strip_ansi(text: str) -> str:
    """移除 ANSI 转义码，用于 CI 环境下 Rich 着色输出后的字符串检查。"""
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


# ═══════════════════════════════════════════════════════
# 数据源测试共享 fixture
# ═══════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _reset_data_factory():
    """每个测试前重置数据源工厂缓存。"""
    reset_data_factory()
    yield
    reset_data_factory()


@pytest.fixture
def sample_kline_data() -> KlineData:
    return KlineData(
        timestamps=[datetime(2026, 8, 1), datetime(2026, 8, 4)],
        open=[100.0, 102.0],
        high=[103.0, 104.0],
        low=[99.0, 101.0],
        close=[101.0, 103.0],
        volume=[1e6, 1.2e6],
        amount=[1e8, 1.2e8],
    )


@pytest.fixture
def sample_quote() -> RealtimeQuote:
    return RealtimeQuote(
        ticker="sh.600519",
        price=1800.5,
        pe=28.5,
        pb=5.2,
        market_cap=22600.0,
        turnover=0.3,
        source="akshare",
    )


@pytest.fixture
def sample_metadata() -> StockMetadata:
    return StockMetadata(
        ticker="sh.600519",
        industry="白酒",
        industry_code="C16",
        pe_ttm=28.5,
        pb=5.2,
        ipo_date="1999-11-10",
        out_date=None,
        is_st=False,
        source="baostock",
    )


def pytest_configure(config: object) -> None:
    """将缓存和研究成果路由到临时目录，防止 pytest 破坏正式数据。"""
    from tempfile import mkdtemp

    from trade_krono_cli.globals import clear_all_globals

    test_cache = Path(mkdtemp(prefix="tk_cache_"))
    test_results = Path(mkdtemp(prefix="tk_results_"))
    os.environ["TRADING_KRONO_CACHE_DIR"] = str(test_cache)
    os.environ["TRADING_KRONO_RESULTS_DIR"] = str(test_results)
    # 立即清除已有全局单例，确保新 settings 在首次调用时生效
    clear_all_globals()


def pytest_sessionstart(session: object) -> None:  # type: ignore[no-redef]
    """session 启动时校验隔离状态，若 env var 未设置则立即失败。"""
    _session = session  # type: ignore[assignment]
    cache_dir = os.getenv("TRADING_KRONO_CACHE_DIR")
    if not cache_dir:
        _session.config.exitstatus = 1  # type: ignore[attr-defined]
        raise RuntimeError(
            "⛔ 测试环境隔离失败：TRADING_KRONO_CACHE_DIR 环境变量未设置。\n"
            "请检查 tests/conftest.py 的 pytest_configure 是否正确执行。"
        )
    import trade_krono_cli.config as _cfg

    settings = _cfg.get_settings()
    actual = str(settings.cache_dir.resolve())
    expected = Path(cache_dir).resolve()
    if not actual.startswith(str(expected)):
        _session.config.exitstatus = 1  # type: ignore[attr-defined]
        raise RuntimeError(
            f"⛔ 测试环境隔离失败：cache_dir 不匹配！\n  期望前缀：{expected}\n  实际路径：{actual}"
        )


def make_mock_settings(
    cache_dir: Path | None = None,
    results_dir: Path | None = None,
    **overrides,
) -> SimpleNamespace:
    """创建一个模拟 Settings 对象，用于测试依赖注入。"""
    defaults = SimpleNamespace(
        project_root=Path("/tmp/test-project"),
        cache_dir=cache_dir
        or Path(os.getenv("TRADING_KRONO_CACHE_DIR", "/tmp/test-project/outputs/cache")),
        results_dir=results_dir
        or Path(os.getenv("TRADING_KRONO_RESULTS_DIR", "/tmp/test-project/outputs/results")),
        tradingagents_root=Path("/tmp/test-project/external/TradingAgents-astock"),
        kronos_root=Path("/tmp/test-project/external/Kronos"),
        llm_provider="deepseek",
        deep_think_llm="deepseek-chat",
        quick_think_llm="deepseek-chat",
        backend_url=None,
        max_debate_rounds=1,
        max_risk_discuss_rounds=1,
        checkpoint_enabled=True,
        output_language="Chinese",
        kronos_model="kronos-base",
        kronos_tokenizer="kronos-base",
        kronos_device="cpu",
        kronos_lookback=400,
        kronos_pred_len=30,
        kronos_sample_count=5,
        kronos_T=1.0,
        kronos_top_p=0.9,
        kronos_use_sample_confidence=False,
        kronos_batch_size=8,
        default_min_confidence=55.0,
        default_allowed_signals=["BUY", "HOLD"],
        baostock_sleep_sec=1.0,
        memory_log_path=Path("/tmp/test-project/outputs/memory_log.jsonl"),
        # ── 重试策略配置 ───────────────────────────────────
        retry_max_attempts=3,
        retry_base_delay=2.0,
        retry_jitter=True,
        retry_rate_limit_backoff=True,
        retry_rate_limit_max_wait=60.0,
        # ── 数据源配置 ───────────────────────────────────
        data_provider="baostock",
        data_fallback="akshare,mootdx,tushare",
        akshare_enabled=True,
        mootdx_enabled=True,
        # ── 评分策略配置 ───────────────────────────────────
        scoring_strategy="linear",
        risk_boost_strategy="fixed_boost",
        risk_boost_multiplier=1.0,
        risk_boost_diminishing_power=0.5,
        # ── 过滤配置 ─────────────────────────────────────
        filter_market_cap_range="",
        filter_industry_whitelist="",
        filter_industry_blacklist="",
        filter_pe_range="",
        filter_pb_range="",
        filter_max_risk_score="",
        filter_min_volume_ratio="",
        filter_exclude_st=True,
        filter_skip_suspended=True,
        filter_skip_new_stock=True,
        filter_new_stock_min_days=60,
        filter_kline_min_completeness=0.85,
        filter_abnormality_risk_boost_enabled=True,
        # ── 降级策略配置 ───────────────────────────────────
        degrade_mode="strict",
        ta_cache_fallback_enabled=False,
        ta_cache_max_age_days=7,
    )
    # 用 overrides 覆盖默认值
    for k, v in overrides.items():
        setattr(defaults, k, v)
    return defaults


def pytest_runtest_setup(item: object) -> None:
    """在每个测试用例 setup 阶段清除全局状态。"""
    from trade_krono_cli.globals import clear_all_globals

    clear_all_globals()


def pytest_runtest_call(item: object) -> None:
    """在每个测试用例调用前（setup 之后）再次清除全局状态，确保干净起点。"""
    from trade_krono_cli.globals import clear_all_globals

    clear_all_globals()
