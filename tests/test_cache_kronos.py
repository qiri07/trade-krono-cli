"""trade_krono_cli.cache.kronos 单元测试。

覆盖 KronosCache.get_kronos / set_kronos：命中、缺失、TTL 过期、数据损坏、
带 config_hash/model_ver 的读写。使用 pytest 隔离的临时缓存目录
(conftest 已通过 TRADING_KRONO_CACHE_DIR 路由)，不写入生产数据库。

所有测试使用唯一的 (ticker, date, pred_len, sample_cnt) 键，避免跨测试
的共享临时数据库污染。
"""

from __future__ import annotations

import time

from trade_krono_cli.cache.base import Cache
from trade_krono_cli.cache.kronos import KronosCache


def _make_cache() -> Cache:
    return Cache()


class TestKronosCacheSetGet:
    def test_set_get_kronos(self) -> None:
        cache = _make_cache()
        kronos = KronosCache(cache)
        result = {"direction": "UP", "expected_change_pct": 3.5}
        kronos.set_kronos(
            "sh.600519", "2026-08-11", 30, result, ttl=0.0, sample_count=1
        )
        out = kronos.get_kronos("sh.600519", "2026-08-11", 30, sample_count=1)
        assert out is not None
        assert out["direction"] == "UP"
        assert out["expected_change_pct"] == 3.5
        cache.close()

    def test_get_kronos_missing_returns_none(self) -> None:
        cache = _make_cache()
        kronos = KronosCache(cache)
        assert kronos.get_kronos("sh.999999", "2026-01-01", 10) is None
        cache.close()

    def test_get_kronos_expired_ttl_returns_none(self) -> None:
        cache = _make_cache()
        kronos = KronosCache(cache)
        kronos.set_kronos(
            "sh.000858", "2026-08-11", 30, {"direction": "UP"}, ttl=-1.0
        )
        assert kronos.get_kronos("sh.000858", "2026-08-11", 30) is None
        cache.close()

    def test_get_kronos_corrupt_json_returns_none(self) -> None:
        cache = _make_cache()
        kronos = KronosCache(cache)
        cache._transaction(
            lambda conn: conn.execute(
                "INSERT OR REPLACE INTO kronos_cache "
                "(ticker, date, pred_len, sample_cnt, config_hash, model_ver, ttl, data, created) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "sz.000001", "2026-08-11", 30, 1, "", "", 86400.0,
                    b"{not valid json", time.time(),
                ),
            )
        )
        assert kronos.get_kronos("sz.000001", "2026-08-11", 30) is None
        cache.close()

    def test_get_kronos_config_hash_model_ver_isolation(self) -> None:
        cache = _make_cache()
        kronos = KronosCache(cache)
        kronos.set_kronos(
            "sh.600519", "2026-08-12", 30, {"v": "a"},
            config_hash="hash_a", model_ver="ver_a",
        )
        kronos.set_kronos(
            "sh.600519", "2026-08-12", 30, {"v": "b"},
            config_hash="hash_b", model_ver="ver_b",
        )
        assert kronos.get_kronos(
            "sh.600519", "2026-08-12", 30,
            config_hash="hash_a", model_ver="ver_a",
        ) == {"v": "a"}
        assert kronos.get_kronos(
            "sh.600519", "2026-08-12", 30,
            config_hash="hash_b", model_ver="ver_b",
        ) == {"v": "b"}
        kronos.set_kronos(
            "sh.600519", "2026-08-12", 30, {"v": "c"}, sample_count=5,
        )
        assert kronos.get_kronos("sh.600519", "2026-08-12", 30, sample_count=1) is None
        assert kronos.get_kronos("sh.600519", "2026-08-12", 30, sample_count=5) == {"v": "c"}
        cache.close()

    def test_set_kronos_default_parameters(self) -> None:
        cache = _make_cache()
        kronos = KronosCache(cache)
        kronos.set_kronos("bj.920001", "2026-08-11", 30, {"direction": "DOWN"})
        out = kronos.get_kronos("bj.920001", "2026-08-11", 30)
        assert out == {"direction": "DOWN"}
        cache.close()
