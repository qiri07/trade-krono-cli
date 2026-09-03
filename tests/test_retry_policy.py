"""重试策略与错误分级测试 — retry_policy 模块的完整覆盖。

覆盖范围：
  · 错误分类：classify_error() 对各类异常的正确归类
  · smart_retry 装饰器：retriable / non_retriable / rate limit 行为
  · FailureStore：记录、查询、清除、统计
  · parse_retry_after：秒数与 HTTP 日期格式解析
  · PipelineConfig retry 字段默认值与 override
  · config_validator retry 校验规则
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import NoReturn
from unittest.mock import patch

import pytest

from trade_krono_cli.config import Settings, clear_settings
from trade_krono_cli.pipeline_config import PipelineConfig
from trade_krono_cli.retry_policy import (
    AuthError,
    DataNotFoundError,
    FailureRecord,
    FailureStore,
    NetworkError,
    ParameterError,
    RateLimitError,
    RetryPolicy,
    Server5xxError,
    ValidationError,
    classify_error,
    clear_failure_store_singleton,
    make_rate_limit_error,
    parse_retry_after,
    smart_retry,
)

# ═══════════════════════════════════════════════════════
# 错误分类测试
# ═══════════════════════════════════════════════════════


class TestClassifyError:
    """verify classify_error returns correct category for each exception type."""

    def test_retryable_explicit_classes(self) -> None:
        assert classify_error(NetworkError("conn failed")) == ("retriable", "conn failed")
        assert classify_error(RateLimitError("429")) == ("retriable", "429")
        assert classify_error(Server5xxError("500")) == ("retriable", "500")

    def test_non_retriable_explicit_classes(self) -> None:
        assert classify_error(ParameterError("bad param")) == ("non_retriable", "bad param")
        assert classify_error(DataNotFoundError("not found")) == ("non_retriable", "not found")
        assert classify_error(AuthError("401")) == ("non_retriable", "401")
        assert classify_error(ValidationError("bad format")) == ("non_retriable", "bad format")

    def test_runtime_error_with_network_msg(self) -> None:
        exc = RuntimeError("connection timeout to server")
        cat, desc = classify_error(exc)
        assert cat == "retriable"
        assert "网络" in desc or "timeout" in desc.lower()

    def test_runtime_error_with_data_not_found_msg(self) -> None:
        exc = RuntimeError("no data for sh.600519")
        cat, _ = classify_error(exc)
        assert cat == "non_retriable"

    def test_runtime_error_with_auth_msg(self) -> None:
        exc = RuntimeError("invalid api key 401 unauthorized")
        cat, _ = classify_error(exc)
        assert cat == "non_retriable"

    def test_runtime_error_with_rate_limit_msg(self) -> None:
        exc = RuntimeError("rate limit exceeded 429")
        cat, _ = classify_error(exc)
        assert cat == "retriable"

    def test_generic_exception_defaults_to_non_retriable(self) -> None:
        exc = ValueError("something went wrong")
        cat, _ = classify_error(exc)
        assert cat == "non_retriable"

    def test_connection_error(self) -> None:
        exc = ConnectionError("network unreachable")
        cat, _ = classify_error(exc)
        assert cat == "retriable"

    def test_5xx_status_in_message(self) -> None:
        exc = RuntimeError("server returned 503 service unavailable")
        cat, _ = classify_error(exc)
        assert cat == "retriable"


# ═══════════════════════════════════════════════════════
# smart_retry 装饰器测试
# ═══════════════════════════════════════════════════════


class TestSmartRetry:
    """Test smart_retry decorator behavior."""

    def test_success_on_first_try(self) -> None:
        policy = RetryPolicy(max_attempts=3, base_delay=0.01, jitter=False)

        @smart_retry(policy)
        def fn() -> int:
            return 42

        assert fn() == 42

    def test_retries_on_retriable_error_then_succeeds(self) -> None:
        policy = RetryPolicy(max_attempts=3, base_delay=0.01, jitter=False)
        call_count = 0

        @smart_retry(policy)
        def fn() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                msg = "connection timeout"
                raise NetworkError(msg)
            return "ok"

        result = fn()
        assert result == "ok"
        assert call_count == 3

    def test_gives_up_after_max_attempts_for_retriable(self) -> None:
        policy = RetryPolicy(max_attempts=2, base_delay=0.01, jitter=False)

        @smart_retry(policy)
        def fn() -> NoReturn:
            msg = "persistent failure"
            raise NetworkError(msg)

        with pytest.raises(NetworkError):
            fn()

    def test_non_retriable_error_raises_immediately(self) -> None:
        policy = RetryPolicy(max_attempts=5, base_delay=0.01, jitter=False)
        call_count = 0

        @smart_retry(policy)
        def fn() -> NoReturn:
            nonlocal call_count
            call_count += 1
            msg = "invalid ticker"
            raise ParameterError(msg)

        with pytest.raises(ParameterError, match="invalid ticker"):
            fn()
        assert call_count == 1  # no retry

    def test_auth_error_raises_immediately(self) -> None:
        policy = RetryPolicy(max_attempts=3, base_delay=0.01, jitter=False)

        @smart_retry(policy)
        def fn() -> NoReturn:
            msg = "token expired"
            raise AuthError(msg)

        with pytest.raises(AuthError):
            fn()

    def test_rate_limit_with_retry_after(self) -> None:
        """Rate limit errors should use Retry-After when available."""
        policy = RetryPolicy(
            max_attempts=3,
            base_delay=10.0,
            jitter=False,
            rate_limit_backoff=True,
            rate_limit_max_wait=5.0,
        )
        call_count = 0
        start = time.monotonic()

        @smart_retry(policy)
        def fn() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                msg = "429"
                raise make_rate_limit_error(msg, headers={"Retry-After": "0.05"})
            return "done"

        result = fn()
        elapsed = time.monotonic() - start
        assert result == "done"
        assert call_count == 2
        # Should have waited ~0.05s for retry-after
        assert elapsed >= 0.03

    def test_rate_limit_without_retry_after_falls_back_to_exponential(self) -> None:
        policy = RetryPolicy(max_attempts=3, base_delay=0.05, jitter=False)
        call_count = 0

        @smart_retry(policy)
        def fn() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                msg = "429 no header"
                raise make_rate_limit_error(msg)
            return "ok"

        result = fn()
        assert result == "ok"
        assert call_count == 3

    def test_non_retryable_error_skipped(self) -> None:
        """skip_non_retriable=True should not retry DataNotFoundError."""
        policy = RetryPolicy(max_attempts=5, base_delay=0.01, jitter=False)

        @smart_retry(policy)
        def fn() -> NoReturn:
            msg = "stock delisted"
            raise DataNotFoundError(msg)

        with pytest.raises(DataNotFoundError):
            fn()

    def test_explicit_retryable_subclass_is_retried(self) -> None:
        """Any TradeKronoRetryableError subclass is retried."""
        policy = RetryPolicy(max_attempts=2, base_delay=0.01, jitter=False)

        @smart_retry(policy)
        def fn() -> NoReturn:
            msg = "502 bad gateway"
            raise Server5xxError(msg, status_code=502)

        with pytest.raises(Server5xxError):
            fn()


# ═══════════════════════════════════════════════════════
# FailureStore 测试
# ═══════════════════════════════════════════════════════


class TestFailureStore:
    """Test FailureStore persistence and query operations."""

    @pytest.fixture
    def store(self, tmp_path):
        clear_failure_store_singleton()
        p = tmp_path / "failures.json"
        s = FailureStore(store_path=p)
        yield s
        clear_failure_store_singleton()

    def test_record_and_list(self, store) -> None:
        store.record("sh.600519", "2026-01-15", "ta", NetworkError("timeout"))
        fails = store.list_fails(date="2026-01-15")
        assert len(fails) == 1
        assert fails[0].ticker == "sh.600519"
        assert fails[0].module == "ta"
        assert fails[0].error_category == "retriable"
        assert fails[0].error_type == "NetworkError"

    def test_record_updates_existing_entry(self, store) -> None:
        store.record("sh.600519", "2026-01-15", "ta", NetworkError("timeout1"))
        store.record("sh.600519", "2026-01-15", "ta", NetworkError("timeout2"), attempt_count=2)
        fails = store.list_fails(date="2026-01-15")
        assert len(fails) == 1
        assert fails[0].attempt_count == 2
        assert "timeout2" in fails[0].error_message

    def test_list_fails_filters_by_module(self, store) -> None:
        store.record("sh.600519", "2026-01-15", "ta", NetworkError("ta err"))
        store.record("sh.000001", "2026-01-15", "kronos", DataNotFoundError("kr err"))
        ta_fails = store.list_fails(module="ta")
        kr_fails = store.list_fails(module="kronos")
        assert len(ta_fails) == 1
        assert len(kr_fails) == 1
        assert ta_fails[0].module == "ta"
        assert kr_fails[0].module == "kronos"

    def test_list_fails_filters_by_category(self, store) -> None:
        store.record("sh.600519", "2026-01-15", "ta", NetworkError("timeout"))
        store.record("sh.000001", "2026-01-15", "ta", ParameterError("bad param"))
        retriable = store.list_fails(category="retriable")
        non_retriable = store.list_fails(category="non_retriable")
        assert len(retriable) == 1
        assert len(non_retriable) == 1

    def test_get_tickers_returns_unique_list(self, store) -> None:
        store.record("sh.600519", "2026-01-15", "ta", NetworkError("e1"))
        store.record("sh.600519", "2026-01-15", "kronos", DataNotFoundError("e2"))
        tickers = store.get_tickers(date="2026-01-15")
        assert tickers == ["sh.600519"]

    def test_clear_for_date(self, store) -> None:
        store.record("sh.600519", "2026-01-15", "ta", NetworkError("e1"))
        store.record("sh.000001", "2026-01-16", "ta", ParameterError("e2"))
        cleared = store.clear_for_date("2026-01-15")
        assert cleared == 1
        assert len(store.list_fails()) == 1
        assert store.list_fails()[0].date == "2026-01-16"

    def test_clear_all(self, store) -> None:
        store.record("sh.600519", "2026-01-15", "ta", NetworkError("e1"))
        store.record("sh.000001", "2026-01-15", "kronos", DataNotFoundError("e2"))
        assert store.clear_all() == 2
        assert store.list_fails() == []

    def test_stats(self, store) -> None:
        store.record("sh.600519", "2026-01-15", "ta", NetworkError("e1"))
        store.record("sh.000001", "2026-01-15", "ta", ParameterError("e2"))
        store.record("sh.000002", "2026-01-15", "kronos", Server5xxError("e3"))
        stats = store.stats()
        assert stats["total"] == 3
        assert stats["retriable"] == 2
        assert stats["non_retriable"] == 1
        assert stats["by_module"]["ta"] == 2
        assert stats["by_module"]["kronos"] == 1

    def test_persistence_across_instances(self, tmp_path) -> None:
        p = tmp_path / "failures.json"
        s1 = FailureStore(store_path=p)
        s1.record("sh.600519", "2026-01-15", "ta", NetworkError("e1"))
        s2 = FailureStore(store_path=p)
        fails = s2.list_fails()
        assert len(fails) == 1
        assert fails[0].ticker == "sh.600519"

    def test_empty_store(self) -> None:
        clear_failure_store_singleton()
        s = FailureStore(store_path=Path("/tmp/nonexistent_retry_test.json"))
        assert s.list_fails() == []
        assert s.stats()["total"] == 0
        clear_failure_store_singleton()


# ═══════════════════════════════════════════════════════
# parse_retry_after 测试
# ═══════════════════════════════════════════════════════


class TestParseRetryAfter:
    def test_integer_seconds(self) -> None:
        assert parse_retry_after("120") == 120.0

    def test_float_seconds(self) -> None:
        assert parse_retry_after("3.5") == 3.5

    def test_empty_string(self) -> None:
        assert parse_retry_after("") is None

    def test_none(self) -> None:
        assert parse_retry_after(None) is None

    def test_http_date_format(self) -> None:
        from datetime import datetime, timedelta, timezone

        future = datetime.now(timezone.utc) + timedelta(seconds=45)
        from email.utils import formatdate

        hdr = formatdate(timeval=future.timestamp(), usegmt=True)
        result = parse_retry_after(hdr)
        assert result is not None
        assert 40 <= result <= 50  # tolerance for clock drift

    def test_unparseable_string(self) -> None:
        assert parse_retry_after("foo") is None


# ═══════════════════════════════════════════════════════
# make_rate_limit_error 测试
# ═══════════════════════════════════════════════════════


class TestMakeRateLimitError:
    def test_with_retry_after_header(self) -> None:
        err = make_rate_limit_error("429", headers={"Retry-After": "30"})
        assert isinstance(err, RateLimitError)
        assert err.retry_after == 30.0

    def test_without_retry_after_header(self) -> None:
        err = make_rate_limit_error("429 rate limited")
        assert isinstance(err, RateLimitError)
        assert err.retry_after is None

    def test_with_x_ratelimit_reset(self) -> None:
        err = make_rate_limit_error("429", headers={"X-RateLimit-Reset": "60"})
        assert err.retry_after == 60.0


# ═══════════════════════════════════════════════════════
# PipelineConfig retry 字段测试
# ═══════════════════════════════════════════════════════


class TestPipelineConfigRetry:
    def test_default_values(self) -> None:
        cfg = PipelineConfig.default()
        assert cfg.retry_max_attempts == 3
        assert cfg.retry_base_delay == 2.0
        assert cfg.retry_jitter is True
        assert cfg.retry_rate_limit_backoff is True
        assert cfg.retry_rate_limit_max_wait == 60.0

    def test_override_retry_fields(self) -> None:
        cfg = PipelineConfig.default().override(
            retry_max_attempts=5,
            retry_base_delay=3.0,
            retry_jitter=False,
        )
        assert cfg.retry_max_attempts == 5
        assert cfg.retry_base_delay == 3.0
        assert cfg.retry_jitter is False

    def test_from_dict(self) -> None:
        data = {
            "retry_max_attempts": 4,
            "retry_base_delay": 1.5,
            "retry_jitter": False,
        }
        cfg = PipelineConfig.from_dict(data)
        assert cfg.retry_max_attempts == 4
        assert cfg.retry_base_delay == 1.5
        assert cfg.retry_jitter is False

    def test_to_dict_roundtrip(self) -> None:
        cfg = PipelineConfig.default().override(retry_max_attempts=2, retry_jitter=False)
        d = cfg.to_dict()
        assert d["retry_max_attempts"] == 2
        assert d["retry_jitter"] is False
        cfg2 = PipelineConfig.from_dict(d)
        assert cfg2.retry_max_attempts == 2
        assert cfg2.retry_jitter is False


# ═══════════════════════════════════════════════════════
# Settings retry 字段测试
# ═══════════════════════════════════════════════════════


class TestSettingsRetry:
    def test_default_env_values(self) -> None:
        clear_settings()
        s = Settings()
        assert s.retry_max_attempts == 3
        assert s.retry_base_delay == 2.0
        assert s.retry_jitter is True
        assert s.retry_rate_limit_backoff is True
        assert s.retry_rate_limit_max_wait == 60.0

    def test_env_override(self) -> None:
        clear_settings()
        with patch.dict(
            "os.environ",
            {
                "RETRY_MAX_ATTEMPTS": "5",
                "RETRY_BASE_DELAY": "3.5",
                "RETRY_JITTER": "false",
                "RETRY_RATE_LIMIT_BACKOFF": "false",
                "RETRY_RATE_LIMIT_MAX_WAIT": "120.0",
            },
        ):
            s = Settings()
            assert s.retry_max_attempts == 5
            assert s.retry_base_delay == 3.5
            assert s.retry_jitter is False
            assert s.retry_rate_limit_backoff is False
            assert s.retry_rate_limit_max_wait == 120.0


# ═══════════════════════════════════════════════════════
# config_validator retry 校验测试
# ═══════════════════════════════════════════════════════


class TestConfigValidatorRetry:
    def test_valid_retry_config(self) -> None:
        from trade_krono_cli.config_validator import _validate_retry_policy

        s = Settings()
        errs = _validate_retry_policy(s)
        assert errs == []

    def test_max_attempts_too_low(self) -> None:
        clear_settings()
        with patch.dict("os.environ", {"RETRY_MAX_ATTEMPTS": "0"}):
            s = Settings()
        from trade_krono_cli.config_validator import _validate_retry_policy

        errs = _validate_retry_policy(s)
        assert any(">= 1" in e for e in errs)

    def test_max_attempts_too_high(self) -> None:
        clear_settings()
        with patch.dict("os.environ", {"RETRY_MAX_ATTEMPTS": "15"}):
            s = Settings()
        from trade_krono_cli.config_validator import _validate_retry_policy

        errs = _validate_retry_policy(s)
        assert any("10" in e and "重试" in e for e in errs)

    def test_base_delay_zero(self) -> None:
        clear_settings()
        with patch.dict("os.environ", {"RETRY_BASE_DELAY": "0"}):
            s = Settings()
        from trade_krono_cli.config_validator import _validate_retry_policy

        errs = _validate_retry_policy(s)
        assert any("> 0" in e for e in errs)

    def test_base_delay_too_large(self) -> None:
        clear_settings()
        with patch.dict("os.environ", {"RETRY_BASE_DELAY": "120.0"}):
            s = Settings()
        from trade_krono_cli.config_validator import _validate_retry_policy

        errs = _validate_retry_policy(s)
        assert any("60" in e for e in errs)

    def test_rate_limit_max_wait_zero(self) -> None:
        clear_settings()
        with patch.dict("os.environ", {"RETRY_RATE_LIMIT_MAX_WAIT": "0"}):
            s = Settings()
        from trade_krono_cli.config_validator import _validate_retry_policy

        errs = _validate_retry_policy(s)
        assert any("RETRY_RATE_LIMIT_MAX_WAIT" in e and "> 0" in e for e in errs)

    def test_rate_limit_max_wait_too_large(self) -> None:
        clear_settings()
        with patch.dict("os.environ", {"RETRY_RATE_LIMIT_MAX_WAIT": "500.0"}):
            s = Settings()
        from trade_krono_cli.config_validator import _validate_retry_policy

        errs = _validate_retry_policy(s)
        assert any("300" in e for e in errs)


# ═══════════════════════════════════════════════════════
# FailureRecord 序列化测试
# ═══════════════════════════════════════════════════════


class TestFailureRecord:
    def test_to_dict_and_from_dict(self) -> None:
        rec = FailureRecord(
            ticker="sh.600519",
            date="2026-01-15",
            module="ta",
            error_category="retriable",
            error_type="NetworkError",
            error_message="connection timeout",
            timestamp=1234567890.0,
            attempt_count=3,
        )
        d = rec.to_dict()
        rec2 = FailureRecord.from_dict(d)
        assert rec2.ticker == rec.ticker
        assert rec2.error_category == rec.error_category
        assert rec2.attempt_count == 3

    def test_to_dict_serializable(self) -> None:
        rec = FailureRecord(
            ticker="sh.600519",
            date="2026-01-15",
            module="ta",
            error_category="retriable",
            error_type="NetworkError",
            error_message="test",
        )
        json_str = json.dumps(rec.to_dict(), ensure_ascii=False)
        parsed = json.loads(json_str)
        assert parsed["ticker"] == "sh.600519"
