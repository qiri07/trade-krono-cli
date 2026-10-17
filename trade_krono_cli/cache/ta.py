"""TA 缓存操作 — get_ta / set_ta。

支持版本化缓存，包含：
  · version: 数据版本（用于条件更新）
  · trace_id: 请求追踪 ID（用于审计）
  · input_hash: 输入哈希（用于检测参数变化）
  · conditional upsert: 只有当存储版本 <=  incoming 版本时才更新
"""

from __future__ import annotations

import json
import time
import uuid
from typing import TYPE_CHECKING

from loguru import logger

if TYPE_CHECKING:
    from trade_krono_cli.cache.base import Cache


class TADataCache:
    """TA 分析结果缓存操作集（版本化）。"""

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
            "SELECT data, created, ttl, version FROM ta_cache "
            "WHERE ticker=? AND date=? AND config_hash=? AND prompt_ver=? AND model_ver=?",
            (ticker, date, config_hash, prompt_ver, model_ver),
        )
        if row is None:
            return None
        data, created, ttl, version = row
        if ttl < 0 or (ttl > 0 and time.time() - created > ttl):
            return None
        result = json.loads(data)
        result["_cache_version"] = version
        result["_cache_created"] = created
        return result

    def set_ta(
        self,
        ticker: str,
        date: str,
        result: dict,
        config_hash: str = "",
        prompt_ver: str = "",
        model_ver: str = "",
        ttl: float = 86400,
        input_hash: str = "",
    ) -> None:
        """写入 TA 缓存（条件 upsert）。

        如果已存在相同 key 的记录，只有当 incoming version > stored version
        时才更新，防止旧数据覆盖新数据。

        Parameters
        ----------
        ticker       : 股票代码
        date         : 分析日期
        result       : TA 分析结果 dict
        config_hash  : 配置哈希
        prompt_ver   : prompt 版本
        model_ver    : 模型版本
        ttl          : 缓存过期时间（秒），<0 表示永久
        input_hash   : 输入数据哈希（可选）
        """
        # 生成 trace ID
        trace_id = str(uuid.uuid4())[:8]
        # 计算 incoming version（基于时间戳）
        incoming_version = int(time.time())

        def _upsert(conn) -> None:
            # 检查现有记录
            existing = conn.execute(
                "SELECT version, input_hash FROM ta_cache "
                "WHERE ticker=? AND date=? AND config_hash=? AND prompt_ver=? AND model_ver=?",
                (ticker, date, config_hash, prompt_ver, model_ver),
            ).fetchone()

            if existing:
                stored_version, stored_hash = existing
                # 条件更新：只有当 incoming > stored 且 input_hash 相同时才更新
                if incoming_version > stored_version and (
                    not input_hash or input_hash == stored_hash
                ):
                    conn.execute(
                        "UPDATE ta_cache SET data=?, version=?, trace_id=?, input_hash=?, created=? "
                        "WHERE ticker=? AND date=? AND config_hash=? AND prompt_ver=? AND model_ver=?",
                        (
                            json.dumps(result, ensure_ascii=False).encode(),
                            incoming_version,
                            trace_id,
                            input_hash,
                            time.time(),
                            ticker,
                            date,
                            config_hash,
                            prompt_ver,
                            model_ver,
                        ),
                    )
                    logger.debug(f"📦 TA 缓存更新: {ticker} v{incoming_version}")
                else:
                    logger.debug(
                        f"📦 TA 缓存跳过: {ticker} 版本不更新 (stored={stored_version}, incoming={incoming_version})"
                    )
            else:
                conn.execute(
                    "INSERT INTO ta_cache "
                    "(ticker, date, config_hash, prompt_ver, model_ver, ttl, data, version, trace_id, input_hash, created) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        ticker,
                        date,
                        config_hash,
                        prompt_ver,
                        model_ver,
                        ttl,
                        json.dumps(result, ensure_ascii=False).encode(),
                        incoming_version,
                        trace_id,
                        input_hash,
                        time.time(),
                    ),
                )
                logger.debug(f"📦 TA 缓存写入: {ticker} v{incoming_version} trace={trace_id}")

        self._cache._transaction(_upsert)
