"""trade_krono_cli.cache.ta 单元测试。

覆盖 TADataCache.get_ta / set_ta：命中、缺失、TTL 过期、数据损坏、
版本化条件 upsert、input_hash 变化刷新。使用 pytest 隔离的临时缓存
目录（conftest 已通过 TRADING_KRONO_CACHE_DIR 路由），不写入生产数据库。

所有测试使用唯一的 (ticker, date, config_hash, prompt_ver, model_ver) 键，
避免跨测试的共享临时数据库污染。
"""

from __future__ import annotations

import time

from trade_krono_cli.cache.base import Cache
from trade_krono_cli.cache.ta import TADataCache


def _make_cache() -> Cache:
    return Cache()


class TestTADataCacheSetGet:
    def test_set_get_ta(self) -> None:
        cache = _make_cache()
        ta = TADataCache(cache)
        result = {"score": 90, "signal": "BUY"}
        ta.set_ta("sh.600519", "2026-08-11", result, config_hash="abc")
        out = ta.get_ta("sh.600519", "2026-08-11", config_hash="abc")
        assert out is not None
        assert out["score"] == 90
        assert out["signal"] == "BUY"
        assert "_cache_version" in out
        assert "_cache_created" in out
        cache.close()

    def test_get_ta_missing_returns_none(self) -> None:
        cache = _make_cache()
        ta = TADataCache(cache)
        assert ta.get_ta("sh.999999", "2026-01-01", config_hash="abc") is None
        cache.close()

    def test_get_ta_expired_ttl_returns_none(self) -> None:
        cache = _make_cache()
        ta = TADataCache(cache)
        ta.set_ta("sh.000858", "2026-08-11", {"score": 1}, config_hash="abc", ttl=-1.0)
        assert ta.get_ta("sh.000858", "2026-08-11", config_hash="abc") is None
        cache.close()

    def test_get_ta_corrupt_json_returns_none(self) -> None:
        cache = _make_cache()
        ta = TADataCache(cache)
        cache._transaction(
            lambda conn: conn.execute(
                "INSERT OR REPLACE INTO ta_cache "
                "(ticker, date, config_hash, prompt_ver, model_ver, ttl, data, version, trace_id, input_hash, created) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "sz.000001", "2026-08-11", "abc", "", "",
                    86400.0, b"{not valid json", 1, "trace", "", time.time(),
                ),
            )
        )
        assert ta.get_ta("sz.000001", "2026-08-11", config_hash="abc") is None
        cache.close()

    def test_set_ta_conditional_upsert_version(self) -> None:
        cache = _make_cache()
        ta = TADataCache(cache)
        # 预插入一个低版本记录，确保后续 set_ta 的 incoming_version 更大
        cache._transaction(
            lambda conn: conn.execute(
                "INSERT INTO ta_cache "
                "(ticker, date, config_hash, prompt_ver, model_ver, ttl, data, version, trace_id, input_hash, created) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "sh.600519", "2026-08-12", "abc", "", "",
                    86400.0, b'{"v": 1}', 1, "trace", "", time.time(),
                ),
            )
        )
        ta.set_ta("sh.600519", "2026-08-12", {"v": 2}, config_hash="abc")
        out = ta.get_ta("sh.600519", "2026-08-12", config_hash="abc")
        assert out is not None
        assert out["v"] == 2
        cache.close()

    def test_set_ta_input_hash_change_refreshes(self) -> None:
        cache = _make_cache()
        ta = TADataCache(cache)
        ta.set_ta("sh.000858", "2026-08-12", {"v": 1}, config_hash="abc", input_hash="hash_a")
        out1 = ta.get_ta("sh.000858", "2026-08-12", config_hash="abc")
        assert out1 is not None
        assert out1["v"] == 1

        ta.set_ta("sh.000858", "2026-08-12", {"v": 2}, config_hash="abc", input_hash="hash_b")
        out2 = ta.get_ta("sh.000858", "2026-08-12", config_hash="abc")
        assert out2 is not None
        assert out2["v"] == 2
        cache.close()

    def test_get_ta_with_prompt_ver_model_ver_isolation(self) -> None:
        cache = _make_cache()
        ta = TADataCache(cache)
        ta.set_ta("bj.920001", "2026-08-12", {"v": "p1"}, config_hash="c", prompt_ver="pv1")
        ta.set_ta("bj.920001", "2026-08-12", {"v": "p2"}, config_hash="c", prompt_ver="pv2")
        out1 = ta.get_ta("bj.920001", "2026-08-12", config_hash="c", prompt_ver="pv1")
        out2 = ta.get_ta("bj.920001", "2026-08-12", config_hash="c", prompt_ver="pv2")
        assert out1 is not None and out1["v"] == "p1"
        assert out2 is not None and out2["v"] == "p2"
        cache.close()
