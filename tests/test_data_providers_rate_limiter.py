"""trade_krono_cli.data_providers.rate_limiter 单元测试。

覆盖 RateLimitConfig 与 RateLimiter 的滑动窗口限流、冷却期、统计等逻辑。
所有测试通过显式传入 RateLimitConfig 构造实例，避免依赖全局 Settings。
"""

from __future__ import annotations

import time

from trade_krono_cli.data_providers.rate_limiter import RateLimitConfig, RateLimiter


def _make_config(**overrides) -> RateLimitConfig:
    """构造测试用限流配置，默认值极小以便快速触发限流。"""
    defaults = {
        "max_requests_per_second": 2,
        "min_interval_seconds": 0.05,
        "burst_size": 10,
        "cooldown_seconds": 0.2,
        "max_retries": 3,
        "base_backoff": 0.01,
        "max_backoff": 0.1,
    }
    defaults.update(overrides)
    return RateLimitConfig(**defaults)  # type: ignore[arg-type]


class TestRateLimitConfig:
    """RateLimitConfig 默认值与 from_settings 映射测试。"""

    def test_default_values(self) -> None:
        cfg = RateLimitConfig()
        assert cfg.max_requests_per_second == 2
        assert cfg.min_interval_seconds == 0.5
        assert cfg.burst_size == 10
        assert cfg.cooldown_seconds == 60.0
        assert cfg.max_retries == 5
        assert cfg.base_backoff == 2.0
        assert cfg.max_backoff == 300.0

    def test_custom_values(self) -> None:
        cfg = _make_config(max_requests_per_second=5, cooldown_seconds=10.0)
        assert cfg.max_requests_per_second == 5
        assert cfg.cooldown_seconds == 10.0


class TestRateLimiterAcquire:
    """RateLimiter.acquire 等待时间计算测试。"""

    def test_acquire_immediately_when_empty(self) -> None:
        limiter = RateLimiter(config=_make_config())
        wait = limiter.acquire("provider_a")
        assert wait == 0.0

    def test_acquire_respects_per_second_limit(self) -> None:
        cfg = _make_config(max_requests_per_second=1, min_interval_seconds=0.0)
        limiter = RateLimiter(config=cfg)
        # 第一次立即
        assert limiter.acquire("p") == 0.0
        # 第二次在同一秒内应被限流
        wait = limiter.acquire("p")
        assert wait > 0.0

    def test_acquire_respects_min_interval(self) -> None:
        cfg = _make_config(max_requests_per_second=100, min_interval_seconds=0.5)
        limiter = RateLimiter(config=cfg)
        assert limiter.acquire("p") == 0.0
        wait = limiter.acquire("p")
        assert 0.0 < wait <= 0.5

    def test_acquire_providers_independent(self) -> None:
        limiter = RateLimiter(config=_make_config(max_requests_per_second=1))
        assert limiter.acquire("p1") == 0.0
        # p2 不受 p1 限制（每秒限制为1，但不同 provider 独立计数）
        assert limiter.acquire("p2") == 0.0

    def test_acquire_cooldown_after_429(self) -> None:
        cfg = _make_config(cooldown_seconds=0.2)
        limiter = RateLimiter(config=cfg)
        limiter.record_429("p")
        wait = limiter.acquire("p")
        assert 0.0 < wait <= 0.2
        # 冷却期结束后应立即可用
        time.sleep(0.21)
        assert limiter.acquire("p") == 0.0

    def test_acquire_cooldown_provider_isolation(self) -> None:
        limiter = RateLimiter(config=_make_config(cooldown_seconds=0.2))
        limiter.record_429("p1")
        assert limiter.acquire("p1") > 0.0
        # p2 不受 p1 冷却影响
        assert limiter.acquire("p2") == 0.0

    def test_acquire_window_slide(self) -> None:
        cfg = _make_config(max_requests_per_second=2, min_interval_seconds=0.0)
        limiter = RateLimiter(config=cfg)
        assert limiter.acquire("p") == 0.0
        assert limiter.acquire("p") == 0.0
        # 第三次在同一秒内应被限流
        assert limiter.acquire("p") > 0.0


class TestRateLimiterRecord:
    """record_429 / record_success / get_stats 测试。"""

    def test_record_429_increments_counter(self) -> None:
        limiter = RateLimiter(config=_make_config())
        limiter.record_429("p")
        stats = limiter.get_stats()
        assert stats["total_429s"] == 1
        assert stats["total_throttled"] == 1
        assert "p" in stats["cooldown_providers"]

    def test_get_stats_shape(self) -> None:
        limiter = RateLimiter(config=_make_config())
        limiter.acquire("p")
        limiter.record_429("p")
        stats = limiter.get_stats()
        assert "total_requests" in stats
        assert "total_throttled" in stats
        assert "total_429s" in stats
        assert "cooldown_providers" in stats
        assert "config" in stats
        assert stats["config"]["max_requests_per_second"] == 2


class TestGetRateLimiter:
    """get_rate_limiter 单例测试。"""

    def test_get_rate_limiter_returns_same_instance(self) -> None:
        from trade_krono_cli.data_providers.rate_limiter import get_rate_limiter

        r1 = get_rate_limiter()
        r2 = get_rate_limiter()
        assert r1 is r2
