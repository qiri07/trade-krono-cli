"""Kronos 缓存操作 — get_kronos / set_kronos。"""

from __future__ import annotations

import json
import time

from trade_krono_cli.cache.base import Cache


class KronosCache:
    """Kronos 预测结果缓存操作集。"""

    def __init__(self, cache: Cache) -> None:
        self._cache = cache

    def get_kronos(
        self,
        ticker: str,
        date: str,
        pred_len: int,
        sample_count: int = 1,
        config_hash: str = "",
        model_ver: str = "",
    ) -> dict | None:
        row = self._cache._query_one(
            "SELECT data, created, ttl FROM kronos_cache "
            "WHERE ticker=? AND date=? AND pred_len=? AND sample_cnt=? "
            "AND config_hash=? AND model_ver=?",
            (ticker, date, pred_len, sample_count, config_hash, model_ver),
        )
        if row is None:
            return None
        data, created, ttl = row
        if ttl < 0 or (ttl > 0 and time.time() - created > ttl):
            return None
        return json.loads(data)

    def set_kronos(
        self,
        ticker: str,
        date: str,
        pred_len: int,
        result: dict,
        ttl: float = 86400,
        sample_count: int = 1,
        config_hash: str = "",
        model_ver: str = "",
    ) -> None:
        self._cache._transaction(
            lambda conn: conn.execute(
                "INSERT OR REPLACE INTO kronos_cache "
                "(ticker, date, pred_len, sample_cnt, config_hash, model_ver, ttl, data, created) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    ticker,
                    date,
                    pred_len,
                    sample_count,
                    config_hash,
                    model_ver,
                    ttl,
                    json.dumps(result, ensure_ascii=False).encode(),
                    time.time(),
                ),
            )
        )
