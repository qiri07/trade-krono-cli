#!/usr/bin/env python3
"""daily_analysis LLM 核实模块（Nemotron 统一配置）。

输出四字段 JSON 摘要（analysis_summary/top_picks/risk_alerts/conclusion），
分批调用聚合；限流/配额/重试统一走 llm_rate_limiter。
"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any

from loguru import logger
from openai import RateLimitError

from trade_krono_cli.llm_config import resolve_llm_config

# ── Nemotron API 限流（全局模块，线程安全）─────────────────────────────────────
from trade_krono_cli.llm_rate_limiter import backoff_wait, is_quota_exhausted, wait_for_rate_limit

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None  # type: ignore[misc,assignment]


def _ai_available() -> bool:
    """检查 LLM 是否可用（必须在 _load_env() 之后调用）。"""
    if OpenAI is None:
        return False
    return bool(resolve_llm_config().api_key)


def _get_llm_client() -> Any | None:
    """构建 OpenAI 兼容客户端；Key 与端点按 provider 强匹配。"""
    if OpenAI is None:
        return None
    cfg = resolve_llm_config()
    if not cfg.api_key:
        logger.warning(
            f"LLM 未配置：{cfg.key_env} 为空（端点 {cfg.base_url}），"
            "AI 核实将被跳过；请在 .env 中填入对应 API Key"
        )
        return None
    logger.info(
        f"🔌 LLM 配置: provider={cfg.provider} base_url={cfg.base_url} "
        f"key={cfg.key_env} model={cfg.model}"
    )
    return OpenAI(api_key=cfg.api_key, base_url=cfg.base_url)


def _extract_json(text: str) -> dict | None:
    """从 LLM 响应中提取 JSON，支持代码块、尾随文本等常见格式。"""
    text = text.strip()
    if not text:
        return None
    # 1. 直接解析
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 2. 提取 ```json ... ``` 或 ``` ... ``` 代码块
    block = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text)
    if block:
        try:
            return json.loads(block.group(1).strip())
        except json.JSONDecodeError:
            pass
    # 3. 找第一个 { 和最后一个 } 之间的内容
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        snippet = text[start : end + 1]
        try:
            return json.loads(snippet)
        except json.JSONDecodeError:
            # 常见瑕疵：尾部多余逗号（{"a": 1, } → {"a": 1}）
            cleaned = re.sub(r",\s*}", "}", snippet)
            try:
                return json.loads(cleaned)
            except json.JSONDecodeError:
                pass
    # 4. 对完整文本尝试去掉末尾逗号等常见瑕疵
    cleaned = re.sub(r",\s*}", "}", text)
    cleaned = re.sub(r",\s*$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    return None


def ai_verify(stock_results: list[dict], date_str: str) -> dict[str, str]:
    """调用 LLM 对分析结果进行核实和摘要。

    股票数量超过 AI_VERIFY_BATCH_SIZE（默认 30，env 可覆盖）时自动分批
    调用，结果聚合后返回；全部批次失败时返回失败占位 dict。
    """
    if not _ai_available():
        logger.warning("AI 核实跳过：LLM 未配置")
        return {
            "analysis_summary": "LLM 未配置，跳过 AI 核实",
            "top_picks": "无",
            "risk_alerts": "无",
            "conclusion": "无",
        }

    client = _get_llm_client()
    if client is None:
        return {
            "analysis_summary": "LLM 不可用",
            "top_picks": "无",
            "risk_alerts": "无",
            "conclusion": "无",
        }

    llm_model = resolve_llm_config().model

    _batch_size = int(os.getenv("AI_VERIFY_BATCH_SIZE", "30"))
    batches = [
        stock_results[i : i + _batch_size] for i in range(0, len(stock_results), _batch_size)
    ]

    def _build_prompt(batch: list[dict]) -> str:
        stock_lines = []
        for r in batch:
            ticker = r.get("ticker", "?")
            name = r.get("name", "")
            trend = r.get("trend", "?")
            ma_signal = r.get("ma_signal", "?")
            vol_signal = r.get("vol_signal", "?")
            score = r.get("score", 0)
            stock_lines.append(
                f"• {ticker} {name}：趋势={trend}  "
                f"均线信号={ma_signal}  量能信号={vol_signal}  综合分={score:.1f}"
            )
        stock_text = "\n".join(stock_lines) or "（无数据）"
        return (
            f"以下是 {date_str} 股票的技术分析结果，请给出综合评估：\n\n"
            f"{stock_text}\n\n"
            "请按以下格式输出严格合法的 JSON（不要有任何额外文字）：\n"
            "{\n"
            '  "analysis_summary": "整体市场概况一句话总结",\n'
            '  "top_picks": "推荐Top 3股票及简短理由，无则写"无明确推荐"",\n'
            '  "risk_alerts": "需关注的主要风险，如无写无明显异常风险",\n'
            '  "conclusion": "简要评价1-2句话"\n'
            "}\n"
        )

    def _call_one(prompt: str) -> dict | None:
        wait_for_rate_limit()
        for attempt in range(1, 6):
            try:
                response = client.chat.completions.create(
                    model=llm_model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.1,
                    # 推理型模型（Nemotron 等）会先消耗 token 产出 reasoning_content，
                    # 批量（20 只）时 800 会在 JSON 输出中途被截断（finish_reason=length），
                    # 故需留足预算。
                    max_tokens=4096,
                    timeout=90,
                )
                raw = (response.choices[0].message.content or "").strip()
                result = _extract_json(raw)
                if result:
                    return result
                logger.warning(
                    f"AI 核实 JSON 解析失败（attempt {attempt}），原始响应前200字: {raw[:200]}"
                )
                break
            except RateLimitError as e:
                # 账户额度耗尽/付费墙类型：短暂等待无法恢复，快速失败并明确提示
                if is_quota_exhausted(e):
                    logger.error(
                        "AI 核实触发 LLM 免费账户额度限制，等待无法恢复："
                        f" {str(e)[:120]}。请升级 Token Plan 或更换 API Key 后重试。"
                    )
                    break
                # 普通频率限流（429）：指数退避，最多重试 5 次
                if attempt >= 5:
                    logger.warning(f"AI 核实限流（5次退避重试后仍失败）: {e}")
                    break
                wait = backoff_wait(attempt)
                logger.warning(f"AI 核实触发频率限流，{wait:.1f}s 后重试 ({attempt}/5)...")
                time.sleep(wait)
            except Exception as e:
                logger.warning(f"AI 核实调用失败（attempt {attempt}）: {type(e).__name__}: {e}")
        return None

    # 分批调用并聚合
    agg_summary: list[str] = []
    agg_top_picks: list[str] = []
    agg_risk_alerts: set[str] = set()
    agg_conclusion: str = ""
    failed_batches = 0

    for i, batch in enumerate(batches):
        logger.info(f"🤖 AI 核实 批次 {i + 1}/{len(batches)}（{len(batch)}只）...")
        prompt = _build_prompt(batch)
        result = _call_one(prompt)
        if result:
            s = result.get("analysis_summary", "")
            if s and s not in ("无", "无数据"):
                agg_summary.append(str(s))
            t = result.get("top_picks", "")
            if t and t != "无" and t != "无明确推荐":
                agg_top_picks.append(str(t))
            r = result.get("risk_alerts", "")
            if r and r != "无" and r != "无明显异常风险":
                agg_risk_alerts.add(str(r))
            c = result.get("conclusion", "")
            if c:
                agg_conclusion = str(c)
        else:
            failed_batches += 1

    if failed_batches == len(batches):
        logger.warning("AI 核实全部批次失败")
        return {
            "analysis_summary": "AI 核实失败",
            "top_picks": "无",
            "risk_alerts": "无",
            "conclusion": "无",
        }

    return {
        "analysis_summary": "；".join(agg_summary[:3]) if agg_summary else "AI 核实完成",
        "top_picks": "；".join(agg_top_picks[:5]) if agg_top_picks else "无",
        "risk_alerts": "；".join(sorted(agg_risk_alerts)) if agg_risk_alerts else "无明显异常风险",
        "conclusion": agg_conclusion or "无",
    }
