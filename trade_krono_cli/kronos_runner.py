"""
Kronos 金融时序预测封装层：
  • 模型懒加载 + 显存友好
  • 批量 predict（GPU/CPU 自动切换）
  • 结果结构化 + 缓存
  • 集成 data.py 拉 K 线
"""
from __future__ import annotations

import sys
import time
import json
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional, Any

import numpy as np
import pandas as pd

from loguru import logger
from trade_krono_cli.config import get_settings
from trade_krono_cli.security import validate_ticker, validate_date, retry
from trade_krono_cli.cache import get_cache
from trade_krono_cli.data import fetch_lookback, next_business_days

# Kronos 模块懒加载
_KRONOS_IMPORTED = False


def _ensure_kronos_import() -> None:
    global _KRONOS_IMPORTED
    if _KRONOS_IMPORTED:
        return
    s = get_settings()
    kronos_root = s.kronos_root
    if str(kronos_root) not in sys.path:
        sys.path.insert(0, str(kronos_root))
    _KRONOS_IMPORTED = True
    logger.debug(f"Kronos 路径已加入: {kronos_root}")


@dataclass
class KronosForecastResult:
    """单只股票的 Kronos 预测结果。"""
    ticker: str
    eval_date: str
    horizon: int
    interval: str = "d"
    last_close: Optional[float] = None
    predicted_close_mean: Optional[float] = None
    predicted_close_final: Optional[float] = None
    expected_change_pct: Optional[float] = None
    direction: Optional[str] = None     # UP / DOWN / FLAT
    volatility_proxy: Optional[float] = None
    confidence_band: Optional[dict] = None
    forecast_dict: Optional[dict] = None
    model_name: Optional[str] = None
    error: Optional[str] = None
    elapsed_sec: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


class KronosRunner:
    """
    生产级 Kronos 预测器。

    特点：
    - 模型懒加载（首次预测时才加载）
    - GPU/CPU 自动切换
    - 批量推理 + 自动降级逐只预测
    - 多 sample 取均值 + 置信区间
    """

    def __init__(
        self,
        model_name: Optional[str] = None,
        tokenizer_name: Optional[str] = None,
        device: Optional[str] = None,
        lookback: Optional[int] = None,
        pred_len: Optional[int] = None,
        sample_count: Optional[int] = None,
        T: Optional[float] = None,
        top_p: Optional[float] = None,
        fallback_cpu: bool = True,
        use_cache: bool = True,
    ):
        s = get_settings()
        self.model_name = model_name or s.kronos_model
        self.tokenizer_name = tokenizer_name or s.kronos_tokenizer
        self.device_pref = (device or s.kronos_device).lower()
        self.lookback = lookback or s.kronos_lookback
        self.pred_len = pred_len or s.kronos_pred_len
        self.sample_count = sample_count or s.kronos_sample_count
        self.T = T if T is not None else s.kronos_T
        self.top_p = top_p if top_p is not None else s.kronos_top_p
        self.fallback_cpu = fallback_cpu
        self.use_cache = use_cache

        self._cache = get_cache()
        self._predictor = None
        self._device = None
        self._max_context = 512

        # 修复模型名格式
        if "large" in self.model_name.lower():
            logger.warning("⚠️  Kronos-large 未开源，强制切换为 Kronos-base")
            self.model_name = "kronos-base"

        logger.info(
            f"🧠 KronosRunner 就绪 | model={self.model_name} "
            f"device={self.device_pref} lookback={self.lookback} "
            f"pred_len={self.pred_len}"
        )

    def _resolve_device(self) -> str:
        if self.device_pref.startswith("cuda"):
            try:
                import torch
                if torch.cuda.is_available():
                    return self.device_pref
                logger.warning("⚠️  CUDA 不可用，回退到 CPU")
            except ImportError:
                pass
        return "cpu"

    def _load(self) -> None:
        """懒加载 Kronos 模型。"""
        if self._predictor is not None:
            return

        _ensure_kronos_import()
        device = self._resolve_device()
        self._device = device

        logger.info(f"⏳ 加载 Kronos 模型（首次约 1-3 分钟）...")
        t0 = time.time()

        try:
            import torch
            from model import Kronos, KronosTokenizer, KronosPredictor
            import json
            import os

            models_dir = self._settings.kronos_root / "models"

            # 加载 tokenizer
            tokenizer_cfg_path = models_dir / self.tokenizer_name / "config.json"
            with open(tokenizer_cfg_path, "r") as f:
                tokenizer_cfg = json.load(f)
            self._tokenizer = KronosTokenizer(**tokenizer_cfg)
            weight_path = models_dir / self.tokenizer_name / "model.safetensors"
            self._tokenizer.load_state_dict(
                torch.load(weight_path, map_location="cpu")
            )

            # 加载模型
            model_cfg_path = models_dir / self.model_name / "config.json"
            with open(model_cfg_path, "r") as f:
                model_cfg = json.load(f)
            self._model = Kronos(**model_cfg)
            weight_path = models_dir / self.model_name / "model.safetensors"
            self._model.load_state_dict(
                torch.load(weight_path, map_location="cpu")
            )

            # 推断 max_context
            name = self.model_name.lower()
            if "mini" in name:
                self._max_context = 2048
            else:
                self._max_context = 512

            self._predictor = KronosPredictor(
                self._model, self._tokenizer,
                device=device, max_context=self._max_context,
            )
            logger.info(
                f"✅ Kronos 模型加载完成 ({time.time()-t0:.1f}s, device={device})"
            )

        except ImportError as e:
            raise RuntimeError(
                f"无法导入 Kronos 模块：{e}。"
                f"请确认已安装 Kronos（pip install -e {self._settings.kronos_root}）"
            ) from e

    @property
    def _settings(self):
        return get_settings()

    def _prepare(
        self, ticker: str, eval_date: str
    ) -> tuple[pd.DataFrame, pd.Series, pd.Series, float]:
        """拉 K 线 + 构造 x/y timestamp。"""
        df = fetch_lookback(
            ticker, eval_date,
            lookback=self.lookback,
            frequency="d",
            use_cache=self.use_cache,
        )
        if len(df) < self.lookback:
            raise RuntimeError(
                f"数据不足: {ticker} 仅 {len(df)} 行 < {self.lookback}"
            )

        x_df = df.iloc[-self.lookback:][[
            "open", "high", "low", "close", "volume", "amount"
        ]].reset_index(drop=True)
        x_ts = df.iloc[-self.lookback:]["timestamps"].reset_index(drop=True)
        last_close = float(x_df["close"].iloc[-1])

        last_dt = x_ts.iloc[-1]
        future = next_business_days(last_dt.strftime("%Y-%m-%d"), self.pred_len)
        future = future[:self.pred_len]
        y_ts = pd.Series(future, name="y_timestamp")

        return x_df, x_ts, y_ts, last_close

    def _parse_pred_df(
        self, pred_df: pd.DataFrame, last_close: float
    ) -> dict:
        closes = pred_df["close"].astype(float).values
        if len(closes) == 0:
            raise RuntimeError("Kronos 返回空预测")

        final_close = float(closes[-1])
        mean_close = float(np.mean(closes))
        change_pct = (final_close - last_close) / last_close * 100.0
        direction = "UP" if change_pct > 1.0 else ("DOWN" if change_pct < -1.0 else "FLAT")
        vol = float(np.std(closes))

        q_low = float(np.percentile(closes, 25)) if len(closes) > 1 else mean_close
        q_high = float(np.percentile(closes, 75)) if len(closes) > 1 else mean_close

        return {
            "predicted_close_mean": round(mean_close, 4),
            "predicted_close_final": round(final_close, 4),
            "expected_change_pct": round(change_pct, 3),
            "direction": direction,
            "volatility_proxy": round(vol, 4),
            "confidence_band": {
                "low": round(q_low, 4),
                "high": round(q_high, 4),
            },
        }

    def _pred_df_to_dict(self, pred_df: pd.DataFrame) -> dict:
        idx = pred_df.index
        if not isinstance(idx, pd.DatetimeIndex):
            idx = pd.date_range("today", periods=len(pred_df), freq="B")
        return {
            "timestamps": [t.isoformat() for t in idx],
            "open":   [round(float(x), 4) for x in pred_df.get("open", pd.Series(0)).tolist()],
            "high":   [round(float(x), 4) for x in pred_df.get("high", pd.Series(0)).tolist()],
            "low":    [round(float(x), 4) for x in pred_df.get("low", pd.Series(0)).tolist()],
            "close":  [round(float(x), 4) for x in pred_df.get("close", pd.Series(0)).tolist()],
            "volume": [round(float(x), 2) for x in pred_df.get("volume", pd.Series(0)).tolist()],
        }

    @retry(max_attempts=2, base_delay=3.0, exceptions=(RuntimeError, ConnectionError))
    def predict_one(self, ticker: str, eval_date: str) -> KronosForecastResult:
        ticker = validate_ticker(ticker)
        eval_date = validate_date(eval_date)
        res = KronosForecastResult(
            ticker=ticker, eval_date=eval_date,
            horizon=self.pred_len, interval="d",
            model_name=self.model_name,
        )
        t0 = time.time()

        # 缓存检查
        if self.use_cache and self._cache:
            cached = self._cache.get_kronos(ticker, eval_date, self.pred_len)
            if cached:
                logger.debug(f"📦 Kronos 缓存命中: {ticker}")
                for k, v in cached.items():
                    setattr(res, k, v)
                res.elapsed_sec = 0.0
                return res

        try:
            self._load()
            x_df, x_ts, y_ts, last_close = self._prepare(ticker, eval_date)

            # 多 sample 取均值
            all_paths = []
            for _s in range(max(1, self.sample_count)):
                pred_df = self._predictor.predict(
                    df=x_df, x_timestamp=x_ts, y_timestamp=y_ts,
                    pred_len=len(y_ts), T=self.T, top_p=self.top_p,
                    sample_count=1, verbose=False,
                )
                all_paths.append(pred_df["close"].astype(float).values)

            if len(all_paths) > 1:
                avg_close = np.mean(np.stack(all_paths), axis=0)
                pred_df = pred_df.copy()
                pred_df["close"] = avg_close

            parsed = self._parse_pred_df(pred_df, last_close)
            res.last_close = last_close
            for k, v in parsed.items():
                setattr(res, k, v)
            res.forecast_dict = self._pred_df_to_dict(pred_df)

            # 写缓存
            if self._cache:
                self._cache.set_kronos(
                    ticker, eval_date, self.pred_len, res.to_dict()
                )

        except Exception as e:
            res.error = f"{type(e).__name__}: {e}"
            logger.error(f"❌ Kronos 预测失败 {ticker}: {res.error}")
        finally:
            res.elapsed_sec = round(time.time() - t0, 2)

        return res

    def predict_batch(
        self,
        tickers: list[str],
        eval_date: str,
        stop_on_error: bool = False,
    ) -> list[KronosForecastResult]:
        """
        批量预测：先准备数据，再批量推理。
        失败时自动降级为逐只预测。
        """
        eval_date = validate_date(eval_date)
        tickers = [validate_ticker(t) for t in tickers]
        logger.info(f"🚀 Kronos 批量预测: {len(tickers)} 只, date={eval_date}")

        results: list[KronosForecastResult] = []
        prepared: list[tuple[str, Any, Any, Any, float]] = []

        # 1) 数据准备阶段
        for tk in tickers:
            res = KronosForecastResult(
                ticker=tk, eval_date=eval_date,
                horizon=self.pred_len, interval="d",
                model_name=self.model_name,
            )
            # 缓存检查
            if self.use_cache and self._cache:
                cached = self._cache.get_kronos(tk, eval_date, self.pred_len)
                if cached:
                    for k, v in cached.items():
                        setattr(res, k, v)
                    results.append(res)
                    prepared.append(None)
                    continue

            try:
                x_df, x_ts, y_ts, last_close = self._prepare(tk, eval_date)
                prepared.append((tk, x_df, x_ts, y_ts, last_close))
                results.append(res)
            except Exception as e:
                res.error = f"{type(e).__name__}: {e}"
                logger.error(f"❌ 数据准备失败 {tk}: {res.error}")
                results.append(res)
                prepared.append(None)
                if stop_on_error:
                    return results

        # 2) GPU 批量推理
        valid_items = [(p, i) for i, p in enumerate(prepared) if p is not None]
        if not valid_items:
            return results

        try:
            self._load()
            df_list = [p[0] for p, _ in valid_items]
            x_ts_list = [p[1] for p, _ in valid_items]
            y_ts_list = [p[2] for p, _ in valid_items]
            last_closes = [p[3] for p, _ in valid_items]

            logger.info(f"⏳ GPU 批量推理 {len(df_list)} 只...")
            t0 = time.time()

            pred_dfs = self._predictor.predict_batch(
                df_list=df_list,
                x_timestamp_list=x_ts_list,
                y_timestamp_list=y_ts_list,
                pred_len=len(y_ts_list[0]),
                T=self.T,
                top_p=self.top_p,
                sample_count=1,
                verbose=False,
            )
            logger.info(f"✅ 批量推理完成 ({time.time()-t0:.1f}s)")

            for (_, idx), pred_df, lc in zip(valid_items, pred_dfs, last_closes):
                res = results[idx]
                parsed = self._parse_pred_df(pred_df, lc)
                res.last_close = lc
                for k, v in parsed.items():
                    setattr(res, k, v)
                res.forecast_dict = self._pred_df_to_dict(pred_df)
                if self._cache:
                    self._cache.set_kronos(
                        res.ticker, eval_date, self.pred_len, res.to_dict()
                    )

        except Exception as e:
            logger.warning(f"⚠️  predict_batch 失败({e})，降级为逐只推理")
            for (_, idx) in valid_items:
                tk = results[idx].ticker
                results[idx] = self.predict_one(tk, eval_date)

        success = sum(1 for r in results if r.error is None)
        logger.info(f"📊 Kronos 批量完成: 成功 {success}/{len(tickers)}")
        return results

    def save_results(self, results: list[KronosForecastResult], path: str) -> str:
        from pathlib import Path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        data = [r.to_dict() for r in results]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"💾 Kronos 预测已保存: {path}")
        return path
