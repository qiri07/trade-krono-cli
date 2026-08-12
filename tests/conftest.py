"""共享测试辅助函数。"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace


def make_mock_settings(
    cache_dir: Path | None = None,
    results_dir: Path | None = None,
    **overrides,
) -> SimpleNamespace:
    """创建一个模拟 Settings 对象，用于测试依赖注入。"""
    defaults = SimpleNamespace(
        project_root=Path("/tmp/test-project"),
        cache_dir=cache_dir or Path("/tmp/test-project/outputs/cache"),
        results_dir=results_dir or Path("/tmp/test-project/outputs/results"),
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
        default_min_confidence=55.0,
        default_allowed_signals=["BUY", "HOLD"],
        baostock_sleep_sec=1.0,
        memory_log_path=Path("/tmp/test-project/outputs/memory_log.jsonl"),
    )
    # 用 overrides 覆盖默认值
    for k, v in overrides.items():
        setattr(defaults, k, v)
    return defaults


def pytest_configure(config: object) -> None:
    """在每个测试用例运行前清除全局状态，防止全局污染。"""
    from trade_krono_cli.globals import clear_all_globals
    clear_all_globals()


def pytest_runtest_setup(item: object) -> None:
    """在每个测试用例 setup 阶段清除全局状态。"""
    from trade_krono_cli.globals import clear_all_globals
    clear_all_globals()


def pytest_runtest_call(item: object) -> None:
    """在每个测试用例调用前（setup 之后）再次清除全局状态，确保干净起点。"""
    from trade_krono_cli.globals import clear_all_globals
    clear_all_globals()

