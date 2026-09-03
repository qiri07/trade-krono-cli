"""测试安全工具。"""

from typing import NoReturn

import pytest

from trade_krono_cli.security import (
    KeyVault,
    TokenBucket,
    retry,
    ticker_hash,
    validate_date,
    validate_ticker,
)


def test_validate_ticker_formats() -> None:
    assert validate_ticker("600519") == "sh.600519"
    assert validate_ticker("sh.600519") == "sh.600519"
    assert validate_ticker("000858") == "sz.000858"
    assert validate_ticker("sz.000858") == "sz.000858"
    assert validate_ticker("  600519  ") == "sh.600519"
    assert validate_ticker("300207") == "sz.300207"
    assert validate_ticker("688981") == "sh.688981"


def test_validate_ticker_invalid() -> None:
    with pytest.raises(ValueError):
        validate_ticker("abc")
    with pytest.raises(ValueError):
        validate_ticker("12345")
    with pytest.raises(ValueError):
        validate_ticker("")


def test_validate_date() -> None:
    assert validate_date("2026-08-11") == "2026-08-11"
    assert validate_date("  2026-08-11  ") == "2026-08-11"


def test_validate_date_invalid() -> None:
    with pytest.raises(ValueError):
        validate_date("2026/08/11")
    with pytest.raises(ValueError):
        validate_date("abc")
    with pytest.raises(ValueError):
        validate_date("")


def test_ticker_hash() -> None:
    h1 = ticker_hash("sh.600519")
    h2 = ticker_hash("sh.600519")
    h3 = ticker_hash("sz.000858")
    assert h1 == h2
    assert h1 != h3
    assert len(h1) == 12


def test_token_bucket() -> None:
    bucket = TokenBucket(rate=10.0, capacity=5.0)
    for _ in range(5):
        bucket.acquire()  # 应该不阻塞
    # 第 6 次应该需要等待
    import time

    start = time.monotonic()
    bucket.acquire()
    elapsed = time.monotonic() - start
    assert elapsed >= 0.05  # 至少等待 50ms


def test_retry_success() -> None:
    call_count = 0

    @retry(max_attempts=3, base_delay=0.01)
    def flaky_fn() -> str:
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            msg = "temporary error"
            raise ValueError(msg)
        return "success"

    result = flaky_fn()
    assert result == "success"
    assert call_count == 3


def test_retry_exhausted() -> None:
    @retry(max_attempts=2, base_delay=0.01)
    def always_fails() -> NoReturn:
        msg = "permanent error"
        raise ValueError(msg)

    with pytest.raises(ValueError, match="permanent error"):
        always_fails()


def test_key_vault() -> None:
    vault = KeyVault()
    result = vault.validate()
    assert isinstance(result, dict)
    # 至少有一个 key 字段
    assert len(result) > 0


def test_sanitize_for_log_no_secrets() -> None:
    from trade_krono_cli.security import sanitize_for_log

    assert sanitize_for_log("some normal message") == "some normal message"
    assert sanitize_for_log("no secrets here") == "no secrets here"


def test_sanitize_for_log_redacts_sk_key() -> None:
    from trade_krono_cli.security import sanitize_for_log

    msg = "Error: sk-abc123def456ghi789jkl012mno345pqr678stu failed"
    result = sanitize_for_log(msg)
    assert "sk-" not in result
    assert "[REDACTED_KEY]" in result
    # original message length preserved minus the redacted part
    assert "Error:" in result
    assert "failed" in result


def test_sanitize_for_log_redacts_bearer() -> None:
    from trade_krono_cli.security import sanitize_for_log

    msg = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.test"
    result = sanitize_for_log(msg)
    assert "Bearer" not in result
    assert "[REDACTED_KEY]" in result


def test_sanitize_for_log_redacts_anthropic_key() -> None:
    """Anthropic sk-ant-* 格式的密钥也应被脱敏。"""
    from trade_krono_cli.security import sanitize_for_log

    msg = "Error connecting with sk-ant-api03-XyZ123abc456def789ghiJKLmnopqrstu"
    result = sanitize_for_log(msg)
    assert "sk-ant-" not in result
    assert "[REDACTED_KEY]" in result
    assert "Error connecting with" in result


def test_ensure_import_path() -> None:
    import sys
    from pathlib import Path

    from trade_krono_cli.security import ensure_import_path

    # Use a path that definitely exists (/tmp)
    before = sys.path.copy()
    ensure_import_path(Path("/tmp"))
    assert str(Path("/tmp")) in sys.path
    # Calling again should not duplicate
    ensure_import_path(Path("/tmp"))
    count = sum(1 for p in sys.path if p == "/tmp")
    assert count == 1
    # Non-existent path should be skipped silently
    ensure_import_path(Path("/nonexistent_path_xyz"))
    assert "/nonexistent_path_xyz" not in sys.path
    # Restore
    sys.path[:] = before
