"""测试 llm_request.py — LLM 请求追踪模块。"""

import hashlib

import pytest

from trade_krono_cli.llm_request import (
    LLMRequest,
    build_kronos_llm_request,
    build_ta_llm_request,
    hash_system_prompt,
    hash_user_prompt_structural,
    sha256_hex,
)

# ═══════════════════════════════════════════════════════
# 哈希工具
# ═══════════════════════════════════════════════════════


class TestSha256Hex:
    def test_deterministic(self) -> None:
        h = sha256_hex("hello")
        assert h == hashlib.sha256(b"hello").hexdigest()

    def test_different_inputs(self) -> None:
        assert sha256_hex("a") != sha256_hex("b")

    def test_empty_string(self) -> None:
        h = sha256_hex("")
        assert len(h) == 64  # SHA-256 输出固定 64 字符 hex


class TestHashSystemPrompt:
    def test_basic_hash(self) -> None:
        h = hash_system_prompt("You are a stock analyst.")
        assert len(h) == 64
        assert h != ""

    def test_same_input_same_hash(self) -> None:
        h1 = hash_system_prompt("same prompt")
        h2 = hash_system_prompt("same prompt")
        assert h1 == h2

    def test_whitespace_stripped(self) -> None:
        # strip 首尾空白：前后空格被去掉，所以结果与无空格版本相同
        h1 = hash_system_prompt("  hello world  ")
        h2 = hash_system_prompt("hello world")
        assert h1 == h2  # 首尾空白被 strip，内容相同 → hash 相同

    def test_internal_whitespace_preserved(self) -> None:
        # 内部多余空格保留，哈希不同
        h1 = hash_system_prompt("hello  world")  # 双空格
        h2 = hash_system_prompt("hello world")  # 单空格
        assert h1 != h2


class TestHashUserPromptStructural:
    def test_structural_hash(self) -> None:
        h = hash_user_prompt_structural(
            ticker="sh.600519",
            date="2026-08-11",
            analysts=["market", "news"],
        )
        assert len(h) == 64

    def test_analyst_order_ignored(self) -> None:
        """Analysts 排序后哈希，顺序不影响结果。"""
        h1 = hash_user_prompt_structural(
            ticker="sh.600519",
            date="2026-08-11",
            analysts=["news", "market"],
        )
        h2 = hash_user_prompt_structural(
            ticker="sh.600519",
            date="2026-08-11",
            analysts=["market", "news"],
        )
        assert h1 == h2

    def test_different_ticker_different_hash(self) -> None:
        h1 = hash_user_prompt_structural(ticker="sh.600519", date="2026-08-11", analysts=[])
        h2 = hash_user_prompt_structural(ticker="sz.000858", date="2026-08-11", analysts=[])
        assert h1 != h2


# ═══════════════════════════════════════════════════════
# LLMRequest 数据类
# ═══════════════════════════════════════════════════════


class TestLLMRequest:
    def test_default_values(self) -> None:
        r = LLMRequest()
        assert r.source == "external"
        assert r.provider == ""
        assert r.model == ""
        assert r.success is False
        assert r.latency_sec == 0.0
        assert r.fetched_at != ""

    def test_roundtrip(self) -> None:
        r = LLMRequest(
            source="ta",
            provider="deepseek",
            model="deepseek-chat",
            temperature=0.3,
            success=True,
            latency_sec=2.5,
        )
        d = r.to_dict()
        r2 = LLMRequest.from_dict(d)
        assert r2.source == "ta"
        assert r2.provider == "deepseek"
        assert r2.temperature == 0.3
        assert r2.success is True

    def test_frozen(self) -> None:
        r = LLMRequest(source="ta")
        with pytest.raises(Exception):  # FrozenInstanceError
            r.source = "kronos"  # frozen dataclass 不可修改


class TestBuildTARequest:
    def test_basic(self) -> None:
        r = build_ta_llm_request(
            ticker="sh.600519",
            date="2026-08-11",
            provider="deepseek",
            model="deepseek-chat",
            temperature=None,
            top_p=None,
            system_prompt="You are a stock analyst.",
            analysts=["market", "news"],
            latency_sec=3.2,
            success=True,
        )
        assert r.source == "ta"
        assert r.provider == "deepseek"
        assert r.model == "deepseek-chat"
        assert r.system_prompt_hash == hash_system_prompt("You are a stock analyst.")
        assert r.user_prompt_hash == hash_user_prompt_structural(
            ticker="sh.600519",
            date="2026-08-11",
            analysts=["market", "news"],
        )
        assert r.latency_sec == 3.2
        assert r.success is True

    def test_failure(self) -> None:
        r = build_ta_llm_request(
            ticker="sh.600519",
            date="2026-08-11",
            provider="openai",
            model="gpt-4o",
            temperature=0.7,
            top_p=0.9,
            system_prompt="system",
            analysts=["market"],
            latency_sec=1.0,
            success=False,
            error="Rate limit exceeded",
        )
        assert r.error == "Rate limit exceeded"
        assert r.success is False
        assert r.temperature == 0.7
        assert r.top_p == 0.9


class TestBuildKronosRequest:
    def test_basic(self) -> None:
        r = build_kronos_llm_request(
            ticker="sh.600519",
            date="2026-08-11",
            provider="deepseek",
            model="deepseek-chat",
            temperature=0.1,
            top_p=0.95,
            latency_sec=1.5,
            success=True,
        )
        assert r.source == "kronos"
        assert r.system_prompt_hash == ""  # Kronos 无独立 prompt
        assert r.user_prompt_hash == ""
        assert r.provider == "deepseek"
        assert r.success is True
