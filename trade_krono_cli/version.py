"""
版本追踪 — 量化系统可复现性的基石。

每个分析结果必须回答：
  • 用了什么数据？（data_version）
  • 用了什么模型？（model_version）
  • 用了什么策略？（strategy_version）
  • 用了什么配置？（config_hash）
  • 何时何次运行？（run_id + timestamp）

没有这些，半年后重跑结果不同，无法判断是数据变了还是模型变了。
"""
from __future__ import annotations

import hashlib
import platform
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from loguru import logger


# ═══════════════════════════════════════════════════════
# 项目版本
# ═══════════════════════════════════════════════════════

def get_project_version() -> str:
    """读取项目版本（与 pyproject.toml 和 __init__.py 保持一致）。"""
    try:
        from importlib import metadata
        return metadata.version("trade-krono-cli")
    except Exception:
        pass
    # fallback: 直接读取 __init__.py
    init_path = Path(__file__).parent / "__init__.py"
    if init_path.exists():
        for line in init_path.read_text().splitlines():
            if line.startswith("__version__"):
                return line.split("=")[1].strip().strip('"\'')
    return "0.0.0-dev"


# ═══════════════════════════════════════════════════════
# Run ID 生成
# ═══════════════════════════════════════════════════════

# 每天自动重置计数器
_last_run_id_date: str = ""
_last_run_id_counter: int = 0


def generate_run_id(date: Optional[str] = None) -> str:
    """
    生成格式化的 run_id。

    格式: YYYYMMDD-HHMMSS-NNN
    示例: 20260811-143022-001

    同一天的多次运行自动递增序列号。
    """
    global _last_run_id_date, _last_run_id_counter

    d = date or datetime.now().strftime("%Y-%m-%d")
    today = d.replace("-", "")
    time_str = datetime.now().strftime("%H%M%S")

    if today != _last_run_id_date:
        _last_run_id_date = today
        _last_run_id_counter = 0

    _last_run_id_counter += 1
    seq = f"{_last_run_id_counter:03d}"
    return f"{today}-{time_str}-{seq}"


# ═══════════════════════════════════════════════════════
# Config Hash
# ═══════════════════════════════════════════════════════

# 从配置哈希中排除的敏感字段
_HASH_EXCLUDE_KEYS = {
    "llm_provider", "deep_think_llm", "quick_think_llm",
    "backend_url",
    # API keys 等通过 KeyVault 管理的字段不在此处，跳过
}


def compute_config_hash(
    settings,
    extra: Optional[dict] = None,
) -> str:
    """
    对运行时配置计算哈希，用于标识"本次运行用了什么配置"。

    排除：API keys、敏感路径等。
    包含：模型选择、采样参数、过滤阈值等策略相关配置。
    """
    import os
    h = hashlib.sha256()

    # 核心策略配置
    strategy_keys = [
        "max_debate_rounds",
        "max_risk_discuss_rounds",
        "kronos_model",
        "kronos_device",
        "kronos_lookback",
        "kronos_pred_len",
        "kronos_sample_count",
        "kronos_T",
        "kronos_top_p",
        "kronos_use_sample_confidence",
        "default_min_confidence",
        "output_language",
        "checkpoint_enabled",
    ]
    for k in strategy_keys:
        val = getattr(settings, k, None)
        if val is not None:
            h.update(f"{k}={val}".encode())

    # 自定义 extra
    if extra:
        for k, v in sorted(extra.items()):
            if k not in _HASH_EXCLUDE_KEYS:
                h.update(f"{k}={v}".encode())

    # 环境信息（帮助区分不同机器的运行）
    h.update(platform.system().encode())
    h.update(platform.machine().encode())
    h.update(f"py{platform.python_version()}".encode())

    return h.hexdigest()[:16]


# ═══════════════════════════════════════════════════════
# Data Version
# ═══════════════════════════════════════════════════════

def get_data_version(ticker: str, query_date: str, source: str = "baostock") -> str:
    """
    生成数据版本字符串，用于标识本次分析使用的数据快照。

    格式: {source}-{date}
    例如: baostock-20260811

    如果未来引入多个数据源，可升级为: baostock-20260811|tushare-20260810
    """
    return f"{source}-{query_date}"


# ═══════════════════════════════════════════════════════
# Model Versions
# ═══════════════════════════════════════════════════════

def get_kronos_model_version(
    model_name: str,
    tokenizer_name: str,
    device: str,
) -> str:
    """生成 Kronos 模型版本标识。"""
    return f"kronos-{model_name}-{tokenizer_name}-{device}"


def get_llm_version(
    provider: str,
    deep_model: str,
    quick_model: str,
) -> str:
    """生成 LLM 版本标识。"""
    return f"{provider}/{deep_model}+{quick_model}"


def collect_model_versions(
    kronos_model: str,
    kronos_tokenizer: str,
    kronos_device: str,
    llm_provider: str,
    deep_think_llm: str,
    quick_think_llm: str,
) -> dict:
    """收集所有模型版本信息，返回结构化字典。"""
    return {
        "kronos": get_kronos_model_version(
            kronos_model, kronos_tokenizer, kronos_device
        ),
        "llm": get_llm_version(llm_provider, deep_think_llm, quick_think_llm),
    }


# ═══════════════════════════════════════════════════════
# Prompt Version (TA)
# ═══════════════════════════════════════════════════════

# TA 提示词版本由 TradingAgents-astock 管理，这里记录当前使用的参数组合
# 作为 proxy prompt version

def get_ta_prompt_version(
    max_debate_rounds: int,
    max_risk_discuss_rounds: int,
    output_language: str,
) -> str:
    """
    生成 TA 提示词版本标识。
    实际 prompt 模板版本需从 TradingAgents-astock 获取。
    此处使用关键参数组合作为 proxy。
    """
    return f"ta-v{max_debate_rounds}r{max_risk_discuss_rounds}-{output_language.lower()}"


# ═══════════════════════════════════════════════════════
# 完整 Run Snapshot
# ═══════════════════════════════════════════════════════

def build_run_snapshot(
    date: str,
    settings,
    extra: Optional[dict] = None,
) -> dict:
    """
    构建单次运行的完整版本快照。

    返回：
      run_id           唯一运行标识
      timestamp        ISO 时间戳
      data_version     数据源版本
      model_versions   {kronos, llm} 模型版本
      prompt_version   TA 提示词版本
      strategy_version 策略版本（= 项目版本）
      config_hash      配置哈希
    """
    run_id = generate_run_id(date)
    timestamp = datetime.now().isoformat()

    # 数据版本（以查询日期为快照）
    data_version = get_data_version("generic", date)

    # 模型版本
    model_versions = collect_model_versions(
        kronos_model=settings.kronos_model,
        kronos_tokenizer=settings.kronos_tokenizer,
        kronos_device=settings.kronos_device,
        llm_provider=settings.llm_provider,
        deep_think_llm=settings.deep_think_llm,
        quick_think_llm=settings.quick_think_llm,
    )

    # 提示词版本
    prompt_version = get_ta_prompt_version(
        max_debate_rounds=settings.max_debate_rounds,
        max_risk_discuss_rounds=settings.max_risk_discuss_rounds,
        output_language=settings.output_language,
    )

    # 配置哈希
    config_hash = compute_config_hash(settings, extra=extra)

    return {
        "run_id": run_id,
        "timestamp": timestamp,
        "data_version": data_version,
        "model_versions": model_versions,
        "prompt_version": prompt_version,
        "strategy_version": get_project_version(),
        "config_hash": config_hash,
    }


# ═══════════════════════════════════════════════════════
# 测试友好的重置
# ═══════════════════════════════════════════════════════

def reset_run_id_counter() -> None:
    """测试用：重置当日 run_id 计数器。"""
    global _last_run_id_date, _last_run_id_counter
    _last_run_id_date = ""
    _last_run_id_counter = 0
