"""
Kronos 金融时序预测封装层：
  • 模型懒加载 + 显存友好
  • 批量 predict（GPU/CPU 自动切换）
  • 结果结构化 + 缓存
  • 集成 data.py 拉 K 线
  • 不确定性量化见 prediction_uncertainty.py
"""
from __future__ import annotations

import time
import json
from dataclasses import asdict
from pathlib import Path
from typing import Optional, Any

import numpy as np
import pandas as pd

from loguru import logger
from trade_krono_cli.config import get_settings, Settings
from trade_krono_cli.security import (
    validate_ticker,
    validate_date,
    retry,
    sanitize_for_log,
    ensure_import_path,
)
from trade_krono_cli.cache import get_cache
from trade_krono_cli.data import fetch_lookback, next_business_days
from trade_krono_cli.errors import ModelLoadError, DataError
from trade_krono_cli.prediction_uncertainty import (
    PredictionUncertainty,
    build_result_dict,
)
from trade_krono_cli.adapters import KronosAdapterImpl

# 向后兼容：保持从 kronos_runner 导入 PredictionUncertainty 的能力
__all__ = ("KronosRunner", "KronosForecastResult", "PredictionUncertainty")

# Kronos 模块懒加载（保留，供旧测试兼容）
_KRONOS_IMPORTED = False


def _ensure_kronos_import(settings: Settings) -> None:
    """将 Kronos agent-harness 加入 sys.path。
    （已迁移至 adapters 层；此函数保留供旧测试兼容。）
    """
    global _KRONOS_IMPORTED
    if _KRONOS_IMPORTED:
        return
    from trade_krono_cli.security import ensure_import_path
    harness_root = settings.kronos_root / "agent-harness"
    kronos_root = settings.kronos_root
    ensure_import_path(harness_root, kronos_root)
    _KRONOS_IMPORTED = True
    logger.debug(f"Kronos 路径已加入: {harness_root} + {kronos_root}")


def clear_kronos_imported() -> None:
    """重置 Kronos 懒加载状态，用于测试隔离。"""
    global _KRONOS_IMPORTED
    _KRONOS_IMPORTED = False


# ── 预测结果 ─────────────────────────────────────────────────────────────────

class KronosForecastResult:
    """单只股票的 Kronos 预测结果。"""
    __slots__ = (
        "ticker", "eval_date", "horizon", "interval",
        "last_close", "predicted_close_mean", "predicted_close_final",
        "expected_change_pct", "direction", "volatility_proxy",
        "confidence_band", "forecast_dict", "model_name",
        "error", "elapsed_sec", "prediction_uncertainty",
    )

    def __init__(
        self,
        ticker: str,
        eval_date: str,
        horizon: int,
        interval: str = "d",
        last_close: Optional[float] = None,
        predicted_close_mean: Optional[float] = None,
        predicted_close_final: Optional[float] = None,
        expected_change_pct: Optional[float] = None,
        direction: Optional[str] = None,
        volatility_proxy: Optional[float] = None,
        confidence_band: Optional[dict] = None,
        forecast_dict: Optional[dict] = None,
        model_name: Optional[str] = None,
        error: Optional[str] = None,
        elapsed_sec: float = 0.0,
        prediction_uncertainty: Optional[PredictionUncertainty] = None,
    ):
        self.ticker = ticker
        self.eval_date = eval_date
        self.horizon = horizon
        self.interval = interval
        self.last_close = last_close
        self.predicted_close_mean = predicted_close_mean
        self.predicted_close_final = predicted_close_final
        self.expected_change_pct = expected_change_pct
        self.direction = direction
        self.volatility_proxy = volatility_proxy
        self.confidence_band = confidence_band
        self.forecast_dict = forecast_dict
        self.model_name = model_name
        self.error = error
        self.elapsed_sec = elapsed_sec
        self.prediction_uncertainty = prediction_uncertainty

    def to_dict(self) -> dict:
        d = {
            "ticker": self.ticker,
            "eval_date": self.eval_date,
            "horizon": self.horizon,
            "interval": self.interval,
            "last_close": self.last_close,
            "predicted_close_mean": self.predicted_close_mean,
            "predicted_close_final": self.predicted_close_final,
            "expected_change_pct": self.expected_change_pct,
            "direction": self.direction,
            "volatility_proxy": self.volatility_proxy,
            "confidence_band": self.confidence_band,
            "forecast_dict": self.forecast_dict,
            "model_name": self.model_name,
            "error": self.error,
            "elapsed_sec": self.elapsed_sec,
            "prediction_uncertainty": (
                self.prediction_uncertainty.to_dict()
                if self.prediction_uncertainty is not None
                else None
            ),
        }
        return d


# ── 预测器 ────────────────────────────────────────────────────────────────────

class KronosRunner:
    """
    生产级 Kronos 预测器。

    特点：
    - 模型懒加载（首次预测时才加载）
    - GPU/CPU 自动切换
    - 批量推理 + 自动降级逐只预测
    - 多 sample 取均值 + 真实置信区间（sample_count > 1 时）
    - 预测不确定性量化见 prediction_uncertainty.py
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
        no_cache: bool = False,
        settings: Optional[Settings] = None,
    ):
        self._settings_obj = settings or get_settings()
        self.model_name = model_name or self._settings_obj.kronos_model
        self.tokenizer_name = tokenizer_name or self._settings_obj.kronos_tokenizer
        self.device_pref = (device or self._settings_obj.kronos_device).lower()
        self.lookback = lookback or self._settings_obj.kronos_lookback
        self.pred_len = pred_len or self._settings_obj.kronos_pred_len
        self.sample_count = sample_count or self._settings_obj.kronos_sample_count
        self.T = T if T is not None else self._settings_obj.kronos_T
        self.top_p = top_p if top_p is not None else self._settings_obj.kronos_top_p
        self.fallback_cpu = fallback_cpu
        self.use_cache = use_cache and not no_cache
        self.use_sample_confidence = self._settings_obj.kronos_use_sample_confidence
        self._cache = get_cache()
        self._predictor: Any = None
        self._device: str = "cpu"
        self._max_context = 512
        self._kronos_adapter: Optional[Any] = None  # lazy adapter
        self._device = self._resolve_device()

        if "large" in self.model_name.lower():
            logger.warning("⚠️  Kronos-large 未开源，强制切换为 Kronos-base")
            self.model_name = "kronos-base"

        logger.info(
            f"🧠 KronosRunner 就绪 | model={self.model_name} "
            f"device={self.device_pref} lookback={self.lookback} "
            f"pred_len={self.pred_len} sample_count={self.sample_count}"
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
        """懒加载 Kronos 模型（通过适配器层）。"""
        if self._predictor is not None:
            return

        self._get_adapter().load_model(self._settings)
        self._device = self._kronos_adapter.device if self._kronos_adapter else "cpu"
        logger.info(
            f"✅ Kronos 模型加载完成 (device={self._device})"
        )

    def _get_adapter(self):
        """懒加载 KronosAdapter。"""
        if self._kronos_adapter is None:
            self._kronos_adapter = KronosAdapterImpl()
        return self._kronos_adapter

    @property
    def _settings(self):
        return self.__dict__.get("_settings_obj") or get_settings()

    def _prepare(
        self, ticker: str, eval_date: str
    ) -> tuple[pd.DataFrame, pd.Series, pd.Series, float]:
        """拉 K 线 + 构造 x/y timestamp。"""
        from trade_krono_cli.constraints_config import ConstraintConfig
        adjustflag = ConstraintConfig().adjustflag
        df = fetch_lookback(
            ticker, eval_date,
            lookback=self.lookback,
            frequency="d",
            use_cache=self.use_cache,
            adjustflag=adjustflag,
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

        # ⚠️ 预测日期从 eval_date 起算，而非 x_ts.iloc[-1]
        # 原因：如果股票在 eval_date 前停牌，x_ts.iloc[-1] 会早于 eval_date，
        #       导致 future 窗口起点早于评估日（未来函数/数据泄漏）
        future = next_business_days(eval_date, self.pred_len)
        future = future[:self.pred_len]
        y_ts = pd.Series(future, name="y_timestamp")

        return x_df, x_ts, y_ts, last_close

    def _parse_pred_df(
        self, pred_df: pd.DataFrame, last_close: float,
        sample_count: int = 1,
    ) -> dict:
        """
        从单条预测 DataFrame 解析结果（委托给 prediction_uncertainty 模块）。

        Parameters
        ----------
        pred_df : 预测结果 DataFrame
        last_close : 历史最后一个收盘价
        sample_count : 实际样本数（>1 时填充 path_dispersion）
        """
        closes = pred_df["close"].astype(float).values
        if len(closes) == 0:
            raise RuntimeError("Kronos 返回空预测")

        result = build_result_dict(closes, last_close, sample_count=sample_count)

        # 当 sample_count > 1 时，补填 path_dispersion（基于路径内波动）
        if sample_count > 1:
            mean_close = float(np.mean(closes))
            vol = float(np.std(closes))
            if abs(mean_close) > 1e-8:
                result["prediction_uncertainty"]["path_dispersion"] = round(
                    vol / abs(mean_close), 6
                )
            else:
                result["prediction_uncertainty"]["path_dispersion"] = 0.0

        return result

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

    def _apply_parsed_to_result(
        self, res: KronosForecastResult, parsed: dict,
    ) -> None:
        """将 parsed dict 写入 result，单独处理 prediction_uncertainty。"""
        pu_dict = parsed.pop("prediction_uncertainty", None)
        for k, v in parsed.items():
            setattr(res, k, v)
        if pu_dict:
            res.prediction_uncertainty = PredictionUncertainty(**pu_dict)

    # 向后兼容别名
    _apply_uncertainty = _apply_parsed_to_result

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

        if self.use_cache and self._cache:
            cached = self._cache.get_kronos(ticker, eval_date, self.pred_len, self.sample_count)
            if cached:
                logger.debug(f"📦 Kronos 缓存命中: {ticker}")
                for k, v in cached.items():
                    setattr(res, k, v)
                if isinstance(res.prediction_uncertainty, dict):
                    res.prediction_uncertainty = PredictionUncertainty.from_dict(
                        res.prediction_uncertainty
                    )
                res.elapsed_sec = 0.0
                return res

        try:
            self._load()
            x_df, x_ts, y_ts, last_close = self._prepare(ticker, eval_date)

            n_samples = max(1, self.sample_count)
            adapter = self._get_adapter()

            if n_samples > 1:
                # 多样本：直接委托模型内部处理，避免 N 次独立推理
                pred_df = adapter.predict(
                    df=x_df, x_timestamp=x_ts, y_timestamp=y_ts,
                    pred_len=len(y_ts), T=self.T, top_p=self.top_p,
                    sample_count=n_samples,
                )
                close_vals = pred_df["close"].astype(float).values
                if close_vals.ndim == 2:
                    avg_close = close_vals.mean(axis=0)
                    stacked = close_vals
                else:
                    avg_close = close_vals
                    stacked = close_vals.reshape(1, -1)
            else:
                # 单样本：直接一次调用
                pred_df = adapter.predict(
                    df=x_df, x_timestamp=x_ts, y_timestamp=y_ts,
                    pred_len=len(y_ts), T=self.T, top_p=self.top_p,
                    sample_count=1,
                )
                avg_close = pred_df["close"].astype(float).values
                stacked = avg_close.reshape(1, -1)

            # 计算预测结果（委托给 prediction_uncertainty 模块）
            if n_samples > 1:
                from trade_krono_cli.prediction_uncertainty import (
                    compute_multi_sample,
                    build_uncertainty,
                )
                (
                    change_pct, direction, vol, path_dispersion,
                    direction_confidence, conf_score,
                ) = compute_multi_sample(avg_close, stacked, last_close)

                res.predicted_close_mean = round(float(np.mean(avg_close)), 4)
                res.predicted_close_final = round(float(avg_close[-1]), 4)
                res.expected_change_pct = change_pct
                res.direction = direction
                res.volatility_proxy = vol
                res.confidence_band = {
                    "low": round(float(np.percentile(avg_close, 25)), 4),
                    "high": round(float(np.percentile(avg_close, 75)), 4),
                }
                res.prediction_uncertainty = build_uncertainty(
                    change_pct=change_pct,
                    direction=direction,
                    vol=vol,
                    path_dispersion=path_dispersion,
                    direction_confidence=direction_confidence,
                    confidence_score=conf_score,
                    sample_count=n_samples,
                )
            else:
                parsed = self._parse_pred_df(
                    pd.DataFrame({"close": avg_close}), last_close, sample_count=1
                )
                res.last_close = last_close
                self._apply_parsed_to_result(res, parsed)

                # 重建预测 DataFrame，供 forecast_dict 使用
                y_ts_len = len(y_ts) if hasattr(y_ts, '__len__') else 0
                if y_ts_len == len(avg_close):
                    pred_idx = y_ts.reset_index(drop=True)
                else:
                    pred_idx = pd.date_range("today", periods=len(avg_close), freq="B")
                pred_df = pd.DataFrame({"close": avg_close}, index=pred_idx)

            res.forecast_dict = self._pred_df_to_dict(pred_df)

            if self._cache:
                self._cache.set_kronos(
                    ticker, eval_date, self.pred_len, res.to_dict(),
                    sample_count=self.sample_count,
                )

        except DataError as e:
            res.error = f"{type(e).__name__}: {e}"
            logger.error(f"❌ 数据准备失败 {ticker}: {sanitize_for_log(str(e))}")
        except ModelLoadError as e:
            res.error = f"{type(e).__name__}: {e}"
            logger.error(f"❌ 模型加载失败 {ticker}: {e}")
        except Exception as e:
            res.error = f"{type(e).__name__}: {e}"
            safe_msg = sanitize_for_log(str(e))
            logger.error(f"❌ Kronos 预测失败 {ticker}: {safe_msg}")
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
        prepared: list[tuple[str, Any, Any, Any, float] | None] = []

        for tk in tickers:
            res = KronosForecastResult(
                ticker=tk, eval_date=eval_date,
                horizon=self.pred_len, interval="d",
                model_name=self.model_name,
            )
            if self.use_cache and self._cache:
                cached = self._cache.get_kronos(tk, eval_date, self.pred_len, self.sample_count)
                if cached:
                    for k, v in cached.items():
                        setattr(res, k, v)
                    if isinstance(res.prediction_uncertainty, dict):
                        res.prediction_uncertainty = PredictionUncertainty.from_dict(
                            res.prediction_uncertainty
                        )
                    results.append(res)
                    prepared.append(None)
                    continue

            try:
                x_df, x_ts, y_ts, last_close = self._prepare(tk, eval_date)
                prepared.append((tk, x_df, x_ts, y_ts, last_close))
                results.append(res)
            except DataError as e:
                res.error = f"{type(e).__name__}: {e}"
                logger.error(f"❌ 数据准备失败 {tk}: {sanitize_for_log(str(e))}")
                results.append(res)
            except Exception as e:
                res.error = f"{type(e).__name__}: {e}"
                safe_msg = sanitize_for_log(str(e))
                logger.error(f"❌ 数据准备异常 {tk}: {safe_msg}")
                results.append(res)
                prepared.append(None)
                if stop_on_error:
                    return results

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

            adapter = self._get_adapter()
            pred_dfs = adapter.predict_batch(
                df_list=df_list,
                x_timestamp_list=x_ts_list,
                y_timestamp_list=y_ts_list,
                pred_len=len(y_ts_list[0]),
                T=self.T,
                top_p=self.top_p,
                sample_count=self.sample_count,
            )
            logger.info(f"✅ 批量推理完成 ({time.time()-t0:.1f}s)")

            n_samples = max(1, self.sample_count)
            from trade_krono_cli.prediction_uncertainty import (
                compute_multi_sample,
                build_uncertainty,
            )
            for (_, idx), pred_df, lc in zip(valid_items, pred_dfs, last_closes):
                res = results[idx]
                close_vals = pred_df["close"].astype(float).values
                if n_samples > 1 and close_vals.ndim == 2:
                    avg_close = close_vals.mean(axis=0)
                    stacked = close_vals
                    (
                        change_pct, direction, vol, path_dispersion,
                        direction_confidence, conf_score,
                    ) = compute_multi_sample(avg_close, stacked, lc)

                    res.predicted_close_mean = round(float(np.mean(avg_close)), 4)
                    res.predicted_close_final = round(float(avg_close[-1]), 4)
                    res.expected_change_pct = change_pct
                    res.direction = direction
                    res.volatility_proxy = vol
                    res.confidence_band = {
                        "low": round(float(np.percentile(avg_close, 25)), 4),
                        "high": round(float(np.percentile(avg_close, 75)), 4),
                    }
                    res.prediction_uncertainty = build_uncertainty(
                        change_pct=change_pct,
                        direction=direction,
                        vol=vol,
                        path_dispersion=path_dispersion,
                        direction_confidence=direction_confidence,
                        confidence_score=conf_score,
                        sample_count=n_samples,
                    )
                    res.forecast_dict = self._pred_df_to_dict(pred_df)
                else:
                    parsed = self._parse_pred_df(pred_df, lc, sample_count=1)
                    res.last_close = lc
                    self._apply_parsed_to_result(res, parsed)
                    res.forecast_dict = self._pred_df_to_dict(pred_df)
                if self._cache:
                    self._cache.set_kronos(
                        res.ticker, eval_date, self.pred_len, res.to_dict(),
                        sample_count=self.sample_count,
                    )

        except DataError as e:
            logger.warning(f"⚠️  predict_batch 数据错误({e})，降级为逐只推理")
            for (_, idx) in valid_items:
                tk = results[idx].ticker
                results[idx] = self.predict_one(tk, eval_date)
        except ModelLoadError as e:
            logger.warning(f"⚠️  predict_batch 模型错误({e})，降级为逐只推理")
            for (_, idx) in valid_items:
                tk = results[idx].ticker
                results[idx] = self.predict_one(tk, eval_date)
        except Exception as e:
            logger.warning(
                f"⚠️  predict_batch 失败({sanitize_for_log(str(e))})，降级为逐只推理"
            )
            for (_, idx) in valid_items:
                tk = results[idx].ticker
                results[idx] = self.predict_one(tk, eval_date)

        success = sum(1 for r in results if r.error is None)
        logger.info(f"📊 Kronos 批量完成: 成功 {success}/{len(tickers)}")
        return results

    def save_results(self, results: list[KronosForecastResult], path: str) -> str:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        data = [r.to_dict() for r in results]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"💾 Kronos 预测已保存: {path}")
        return path
