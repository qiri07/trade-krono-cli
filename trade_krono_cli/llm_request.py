"""LLM Request 追踪模块。

记录每次 LLM 调用的完整上下文，用于：
  · 可重复性：知道每次决策由哪个模型、哪些参数产生
  · 调试：对比不同 prompt / model 版本的输出差异
  · 审计：token 用量、延迟、成功率统计

关键字段：
  - system_prompt_hash / user_prompt_hash：不存原文，只存 SHA-256，
    既保护隐私又允许跨次对比是否用了相同的 prompt 模板
  - provider / model / temperature / top_p：复现结果所需的全部生成参数
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

# ── 哈希工具 ──────────────────────────────────────────────────────────────────


def sha256_hex(text: str) -> str:
    """计算字符串的 SHA-256 十六进制摘要。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def hash_system_prompt(system_prompt: str) -> str:
    """对系统提示词做规范化哈希。
    规范化：去除空白差异、转小写标记词，保留语义结构。
    """
    # 保留内容，只 strip 首尾空白
    normalized = system_prompt.strip()
    return sha256_hex(normalized)


def hash_user_prompt_structural(
    ticker: str,
    date: str,
    analysts: list[str],
    **kwargs,
) -> str:
    """对用户提示词的结构性部分做哈希。
    不包含具体的市场数据文本（这些数据每次都不同），
    只哈希决定"这是什么请求"的结构字段。
    """
    struct = {
        "ticker": ticker,
        "date": date,
        "analysts": sorted(analysts),
        **{k: v for k, v in kwargs.items() if v is not None},
    }
    return sha256_hex(json.dumps(struct, sort_keys=True, ensure_ascii=False))


# ── 数据类 ────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LLMRequest:
    """一次 LLM 调用的完整上下文快照。

    字段说明：
      source             — "ta" | "kronos" | "external"
      provider           — "deepseek" | "openai" | "anthropic" 等
      model              — 具体模型名称（如 deepseek-chat）
      temperature        — 采样温度，None 表示未设置
      top_p              — nucleus sampling 参数，None 表示未设置
      system_prompt_hash — 系统提示词 SHA-256（不存原文）
      user_prompt_hash   — 用户提示词结构 SHA-256（不存原文）
      input_tokens       — 输入 token 数，未知时为 None
      output_tokens      — 输出 token 数，未知时为 None
      latency_sec        — 请求耗时（秒）
      success            — 是否成功
      error              — 失败原因（success=False 时有值）
      fetched_at         — ISO 时间戳（UTC）
    """

    source: str = "external"
    provider: str = ""
    model: str = ""
    temperature: float | None = None
    top_p: float | None = None
    system_prompt_hash: str = ""
    user_prompt_hash: str = ""
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_sec: float = 0.0
    success: bool = False
    error: str | None = None
    fetched_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> LLMRequest:
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    def __repr__(self) -> str:
        return (
            f"LLMRequest(source={self.source!r}, provider={self.provider!r}, "
            f"model={self.model!r}, success={self.success}, "
            f"latency={self.latency_sec:.1f}s)"
        )


# ── TA 专用构建器 ─────────────────────────────────────────────────────────────


def build_ta_llm_request(
    ticker: str,
    date: str,
    provider: str,
    model: str,
    temperature: float | None,
    top_p: float | None,
    system_prompt: str,
    analysts: list[str],
    latency_sec: float,
    success: bool,
    error: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> LLMRequest:
    """为 TradingAgents 分析构建 LLMRequest。

    Parameters
    ----------
    ticker        : 股票代码
    date          : 分析日期
    provider      : LLM provider 名称
    model         : 模型名称
    temperature   : 采样温度
    top_p         : nucleus sampling 参数
    system_prompt : 系统提示词原文（用于计算 hash）
    analysts      : 参与分析的分析师列表
    latency_sec   : 请求耗时
    success       : 是否成功
    error         : 失败信息（可选）
    input_tokens  : 输入 token 数（可选）
    output_tokens : 输出 token 数（可选）

    """
    return LLMRequest(
        source="ta",
        provider=provider,
        model=model,
        temperature=temperature,
        top_p=top_p,
        system_prompt_hash=hash_system_prompt(system_prompt),
        user_prompt_hash=hash_user_prompt_structural(ticker=ticker, date=date, analysts=analysts),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_sec=round(latency_sec, 2),
        success=success,
        error=error,
    )


def build_kronos_llm_request(
    ticker: str,
    date: str,
    provider: str,
    model: str,
    temperature: float | None,
    top_p: float | None,
    latency_sec: float,
    success: bool,
    error: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> LLMRequest:
    """为 Kronos 预测构建 LLMRequest（Kronos 无独立 prompt，hash 为空）。"""
    return LLMRequest(
        source="kronos",
        provider=provider,
        model=model,
        temperature=temperature,
        top_p=top_p,
        system_prompt_hash="",
        user_prompt_hash="",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_sec=round(latency_sec, 2),
        success=success,
        error=error,
    )
