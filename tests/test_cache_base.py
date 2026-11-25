"""trade_krono_cli.cache.base 单元测试。

覆盖 Cache 初始化、建表、表名白名单校验、读写 K 线/TA/Kronos、stats、
clear_all、close 等。使用 pytest 隔离的临时缓存目录（conftest 已通过
TRADING_KRONO_CACHE_DIR 路由），不写入生产数据库。
"""

from __future__ import annotations

from trade_krono_cli.cache.base import CACHE_TABLES, Cache, _validate_table_name


class TestValidateTableName:
    def test_allowed_tables(self) -> None:
        assert _validate_table_name("kline_cache", CACHE_TABLES) == "kline_cache"
        assert _validate_table_name("ta_cache", CACHE_TABLES) == "ta_cache"
        assert _validate_table_name("kronos_cache", CACHE_TABLES) == "kronos_cache"

    def test_disallowed_table_raises(self) -> None:
        with __import__("pytest").raises(ValueError, match="Unauthorized table"):
            _validate_table_name("evil_table", CACHE_TABLES)


class TestCache:
    def test_cache_tables_whitelist(self) -> None:
        assert "kline_cache" in CACHE_TABLES
        assert "ta_cache" in CACHE_TABLES
        assert "kronos_cache" in CACHE_TABLES

    def test_set_get_kline(self) -> None:
        import pickle

        cache = Cache()
        df = __import__("pandas").DataFrame(
            {"close": [1.0, 2.0], "open": [1.0, 2.0], "high": [2.0, 3.0],
             "low": [0.5, 1.5], "volume": [1e6, 2e6], "amount": [1e8, 2e8],
             "timestamps": ["2026-08-01", "2026-08-02"]}
        )
        cache.set_kline("sh.600519", "2026-08-01", "2026-08-02", "d", df, ttl=0.0, adjustflag="1")
        out = cache.get_kline("sh.600519", "2026-08-01", "2026-08-02", "d", adjustflag="1")
        assert out is not None
        retrieved = pickle.loads(out[5]) if isinstance(out, tuple) else out
        assert retrieved is not None
        cache.close()

    def test_set_get_ta(self) -> None:
        cache = Cache()
        cache.set_ta("sh.600519", "2026-08-11", {"score": 90}, config_hash="abc")
        out = cache.get_ta("sh.600519", "2026-08-11", config_hash="abc")
        assert out is not None
        assert out.get("score") == 90
        cache.close()

    def test_set_get_kronos(self) -> None:
        cache = Cache()
        cache.set_kronos(
            "sh.600519", "2026-08-11", 30, {"direction": "UP"}, ttl=0.0, sample_count=1
        )
        out = cache.get_kronos("sh.600519", "2026-08-11", 30, sample_count=1)
        assert out is not None
        assert out.get("direction") == "UP"
        cache.close()

    def test_stats(self) -> None:
        cache = Cache()
        s = cache.stats()
        assert isinstance(s, dict)
        cache.close()

    def test_clear_all(self) -> None:
        cache = Cache()
        n = cache.clear_all()
        assert isinstance(n, int)
        cache.close()

    def test_close_idempotent(self) -> None:
        cache = Cache()
        cache.close()
        cache.close()  # 应不抛异常
