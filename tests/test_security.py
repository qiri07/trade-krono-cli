"""测试安全工具。"""
import pytest
from trade_krono_cli.security import (
    validate_ticker,
    validate_date,
    retry,
    TokenBucket,
    KeyVault,
    ticker_hash,
)


def test_validate_ticker_formats():
    assert validate_ticker("600519") == "sh.600519"
    assert validate_ticker("sh.600519") == "sh.600519"
    assert validate_ticker("000858") == "sz.000858"
    assert validate_ticker("sz.000858") == "sz.000858"
    assert validate_ticker("  600519  ") == "sh.600519"
    assert validate_ticker("300207") == "sz.300207"
    assert validate_ticker("688981") == "sh.688981"


def test_validate_ticker_invalid():
    with pytest.raises(ValueError):
        validate_ticker("abc")
    with pytest.raises(ValueError):
        validate_ticker("12345")
    with pytest.raises(ValueError):
        validate_ticker("")


def test_validate_date():
    assert validate_date("2026-08-11") == "2026-08-11"
    assert validate_date("  2026-08-11  ") == "2026-08-11"


def test_validate_date_invalid():
    with pytest.raises(ValueError):
        validate_date("2026/08/11")
    with pytest.raises(ValueError):
        validate_date("abc")
    with pytest.raises(ValueError):
        validate_date("")


def test_ticker_hash():
    h1 = ticker_hash("sh.600519")
    h2 = ticker_hash("sh.600519")
    h3 = ticker_hash("sz.000858")
    assert h1 == h2
    assert h1 != h3
    assert len(h1) == 12


def test_token_bucket():
    bucket = TokenBucket(rate=10.0, capacity=5.0)
    for _ in range(5):
        bucket.acquire()  # 应该不阻塞
    # 第 6 次应该需要等待
    import time
    start = time.monotonic()
    bucket.acquire()
    elapsed = time.monotonic() - start
    assert elapsed >= 0.05  # 至少等待 50ms


def test_retry_success():
    call_count = 0

    @retry(max_attempts=3, base_delay=0.01)
    def flaky_fn():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise ValueError("temporary error")
        return "success"

    result = flaky_fn()
    assert result == "success"
    assert call_count == 3


def test_retry_exhausted():
    @retry(max_attempts=2, base_delay=0.01)
    def always_fails():
        raise ValueError("permanent error")

    with pytest.raises(ValueError, match="permanent error"):
        always_fails()


def test_key_vault():
    vault = KeyVault()
    result = vault.validate()
    assert isinstance(result, dict)
    # 至少有一个 key 字段
    assert len(result) > 0
