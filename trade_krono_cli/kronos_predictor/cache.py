"""缓存管理模块。

负责 Kronos 预测结果的缓存读写。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from trade_krono_cli.cache import get_cache

if TYPE_CHECKING:
    from trade_krono_cli.config import Settings


class KronosCacheManager:
    """Kronos 预测结果缓存管理器。"""

    def __init__(self, settings: "Settings") -> None:
        self._settings = settings
        self._cache = get_cache()

    def get(
        self,
        ticker: str,
        eval_date: str,
        pred_len: int,
        sample_count: int,
        config_hash: str,
        model_ver: str,
    ) -> dict | None:
        """获取缓存的预测结果。"""
        if not self._cache:
            return None
        return self._cache.get_kronos(
            ticker,
            eval_date,
            pred_len,
            sample_count,
            config_hash=config_hash,
            model_ver=model_ver,
        )

    def set(
        self,
        ticker: str,
        eval_date: str,
        result_dict: dict,
    ) -> None:
        """保存预测结果到缓存。"""
        if not self._cache:
            return
        self._cache.set_kronos(
            ticker,
            eval_date,
            self._settings.kronos_pred_len,
            result_dict,
            sample_count=self._settings.kronos_sample_count,
            config_hash=result_dict.get("config_hash", ""),
            model_ver=result_dict.get("model_version", ""),
        )
