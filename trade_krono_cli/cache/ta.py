"""TA 缓存操作 — get_ta / set_ta。"""

from __future__ import annotations

import json
import time

from trade_krono_cli.cache.base import Cache


class TADataCache:
    """TA 分析结果缓存操作集。"""

    def __init__(self, cache: Cache) -> None:
        self._cache = cache

    def get_ta(
        self,
        ticker: str,
        date: str,
        config_hash: str = "",
        prompt_ver: str = "",
        model_ver: str = "",
    ) -> dict | None:
        row = self._cache._query_one(
            "SELECT data, created, ttl FROM ta_cache "
            "WHERE ticker=? AND date=? AND config_hash=? AND prompt_ver=? AND model_ver=?",
            (ticker, date, config_hash, prompt_ver, model_ver),
        )
        if row is None:
            return None
        data, created, ttl = row
        if ttl < 0 or (ttl > 0 and time.time() - created > ttl):
            return None
        return json.loads(data)

    def set_ta(
        self,
        ticker: str,
        date: str,
        result: dict,
        config_hash: str = "",
        prompt_ver: str = "",
        model_ver: str = "",
        ttl: float = 86400,
    ) -> None:
        self._cache._transaction(
            lambda conn: conn.execute(
                "INSERT OR REPLACE INTO ta_cache "
                "(ticker, date, config_hash, prompt_ver, model_ver, ttl, data, created) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    ticker,
                    date,
                    config_hash,
                    prompt_ver,
                    model_ver,
                    ttl,
                    json.dumps(result, ensure_ascii=False).encode(),
                    time.time(),
                ),
            )
        )
