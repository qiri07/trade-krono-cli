"""
Kronos 适配器实现。

封装 cli_anything.kronos 的全部导入和调用，
业务代码只通过 KronosAdapter 与 Kronos 外部项目交互。
"""
from __future__ import annotations

import time
from typing import Any, Optional

import numpy as np
import pandas as pd

from loguru import logger

from trade_krono_cli.adapters.base import KronosAdapter
from trade_krono_cli.errors import ModelLoadError
from trade_krono_cli.security import ensure_import_path


class KronosAdapterImpl(KronosAdapter):
    """基于 cli_anything.kronos 的 Kronos 预测适配器实现。"""

    def __init__(self) -> None:
        self._predictor: Optional[Any] = None
        self._device: str = "cpu"
        self._max_context: int = 512

    # ── 生命周期 ─────────────────────────────────────────────────────────────

    def _resolve_device(self, device_pref: str) -> str:
        """根据设备偏好返回实际可用设备（CUDA 回退 CPU）。"""
        if device_pref.startswith("cuda"):
            try:
                import torch
                if torch.cuda.is_available():
                    return device_pref
                logger.warning("⚠️  CUDA 不可用，回退到 CPU")
            except ImportError:
                pass
        return "cpu"

    def load_model(self, settings: Any) -> None:
        """
        懒加载 Kronos 模型：注入路径 → 导入 load_model → 实例化。
        """
        if self._predictor is not None:
            return

        harness_root = settings.kronos_root / "agent-harness"
        kronos_root = settings.kronos_root
        ensure_import_path(harness_root, kronos_root)
        logger.debug(f"Kronos 路径已加入: {harness_root} + {kronos_root}")

        device = self._resolve_device(settings.kronos_device)
        self._device = device

        logger.info(f"⏳ 加载 Kronos 模型（首次约 1-3 分钟）...")
        t0 = time.time()

        try:
            from cli_anything.kronos.utils.kronos_backend import load_model

            predictor, meta = load_model(
                name=settings.kronos_model.lower(),
                device=device,
            )
            self._predictor = predictor
            self._max_context = meta.get("max_context", 512)
            logger.info(
                f"✅ KronosAdapter 模型加载完成 ({time.time()-t0:.1f}s, device={device})"
            )

        except ImportError as e:
            raise ModelLoadError(
                f"无法导入 cli_anything.kronos：{e}。"
                f"请确认已安装 Kronos agent-harness "
                f"（pip install -e {settings.kronos_root / 'agent-harness'}）"
            ) from e

    @property
    def predictor(self) -> Any:
        """暴露内部预测器，供需要直接调用的场景使用（测试友好）。"""
        return self._predictor

    @property
    def device(self) -> str:
        return self._device

    # ── 接口实现 ─────────────────────────────────────────────────────────────

    def predict(
        self,
        df: Any,
        x_timestamp: Any,
        y_timestamp: Any,
        pred_len: int,
        T: float,
        top_p: float,
        sample_count: int = 1,
    ) -> pd.DataFrame:
        """单只股票预测，返回含 'close' 列的 DataFrame。"""
        if self._predictor is None:
            raise RuntimeError(
                "KronosAdapter 模型尚未加载，请先调用 load_model()"
            )
        return self._predictor.predict(
            df=df,
            x_timestamp=x_timestamp,
            y_timestamp=y_timestamp,
            pred_len=pred_len,
            T=T,
            top_p=top_p,
            sample_count=sample_count,
            verbose=False,
        )

    def predict_batch(
        self,
        df_list: list,
        x_timestamp_list: list,
        y_timestamp_list: list,
        pred_len: int,
        T: float,
        top_p: float,
        sample_count: int = 1,
    ) -> list[pd.DataFrame]:
        """批量预测，返回 DataFrame 列表。"""
        if self._predictor is None:
            raise RuntimeError(
                "KronosAdapter 模型尚未加载，请先调用 load_model()"
            )
        return self._predictor.predict_batch(
            df_list=df_list,
            x_timestamp_list=x_timestamp_list,
            y_timestamp_list=y_timestamp_list,
            pred_len=pred_len,
            T=T,
            top_p=top_p,
            sample_count=sample_count,
            verbose=False,
        )
