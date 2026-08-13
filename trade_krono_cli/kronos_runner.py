"""
Kronos 金融时序预测封装层（业务逻辑层）。

职责边界：
  · 数据准备（fetch_lookback、x/y 构造）
  · 预测调度（单只 / 批量 + 自动降级）
  · 结果解析（_parse_pred_df、forecast_dict）
  · 缓存读写
  · 结果落盘（save_results）

资源管理（模型加载 / 设备判断 / 适配器初始化）由 KronosSession 负责。
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
)
from trade_krono_cli.retry_policy import (
    smart_retry,
    RetryPolicy,
    classify_error,
    get_failure_store,
)
from trade_krono_cli.cache import get_cache
from trade_krono_cli.data import fetch_lookback, next_business_days
from trade_krono_cli.version import compute_config_hash, get_kronos_model_version
from trade_krono_cli.errors import ModelLoadError, DataError
from trade_krono_cli.prediction_uncertainty import (
    PredictionUncertainty,
    build_result_dict,
)

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
    生产级 Kronos 预测器（业务逻辑层）。

    特点：
    - 数据懒加载：首次预测时拉取 K 线
    - GPU/CPU 自动切换（由 KronosSession 管理）
    - 批量推理 + 自动降级逐只预测
    - 多 sample 取均值 + 真实置信区间（sample_count > 1 时）

    资源管理（模型加载 / 设备选择 / 适配器初始化）由 KronosSession 负责。
    """

    def __init__(
        self,
        session: Optional[Any] = None,
        no_cache: bool = False,
        sample_count: Optional[int] = None,
        batch_size: Optional[int] = None,
        settings: Optional[Settings] = None,
        retry_policy: Optional[RetryPolicy] = None,
    ):
        self._session = session
        self._settings_obj = settings or get_settings()
        self.sample_count = sample_count or self._settings_obj.kronos_sample_count
        self.batch_size = batch_size or self._settings_obj.kronos_batch_size
        self.use_cache = not no_cache
        self._cache = get_cache()
        self._device = "cpu"  # fallback，实际由 session 管理
        # 流式流水线预取：K 线数据已提前拉取，避免重复 I/O
        self._pre_fetched: dict[str, pd.DataFrame] = {}
        self.model_name = (
            session._model_name if session else
            (self._settings_obj.kronos_model or "kronos-base")
        )

        if "large" in self.model_name.lower():
            logger.warning("⚠️  Kronos-large 未开源，强制切换为 Kronos-base")
            self.model_name = "kronos-base"

        logger.info(
            f"🧠 KronosRunner 就绪 | model={self.model_name} "
            f"sample_count={self.sample_count}, batch_size={self.batch_size}"
        )

        # 重试策略：CLI 参数 > Settings 默认
        self._retry_policy = retry_policy or RetryPolicy(
            max_attempts=get_settings().retry_max_attempts,
            base_delay=get_settings().retry_base_delay,
            jitter=get_settings().retry_jitter,
            rate_limit_backoff=get_settings().retry_rate_limit_backoff,
            rate_limit_max_wait=get_settings().retry_rate_limit_max_wait,
        )

    # ── 资源访问（委托给 session）─────────────────────────────────────────────

    @property
    def _settings(self):
        return self._settings_obj or get_settings()

    @property
    def _config_hash(self) -> str:
        return compute_config_hash(self._settings_obj)

    @property
    def _model_version(self) -> str:
        return get_kronos_model_version(
            self.model_name,
            self._settings_obj.kronos_tokenizer,
            self._session.device if self._session else "cpu",
        )

    @property
    def _predictor(self) -> Optional[Any]:
        """暴露内部预测器（供 _load 等使用）。"""
        if self._session is not None:
            return self._session.predictor
        return None

    @property
    def _adapter(self) -> Any:
        """暴露适配器实例（供预测调用使用）。"""
        if self._session is not None:
            return self._session.adapter
        raise RuntimeError("KronosSession 未绑定，无法获取适配器")

    def _load(self) -> None:
        """委托 session 执行模型加载。"""
        if self._session is not None:
            self._session.ensure_loaded()
            self._device = self._session.device
        # 无 session 时不做任何操作（测试场景下由 mock 替代）

    # ── 数据准备 ──────────────────────────────────────────────────────────────

    @staticmethod
    def _pad_df_to_length(df: pd.DataFrame, target_len: int) -> pd.DataFrame:
        """将 DataFrame 填充到 target_len 行（重复最后一行）。"""
        if len(df) >= target_len:
            return df.iloc[-target_len:]
        # 保留原始数据，用最后一行填充剩余位置
        last_row = df.iloc[[-1]]
        n_repeats = target_len - len(df)
        pad = pd.concat([last_row] * n_repeats, ignore_index=True)
        return pd.concat([df, pad], ignore_index=True).iloc[:target_len]

    @staticmethod
    def _split_batches(items: list, size: int) -> list[list]:
        """将列表分割为固定大小的批次。"""
        return [items[i:i + size] for i in range(0, len(items), size)]

    def _prepare(
        self, ticker: str, eval_date: str
    ) -> tuple[pd.DataFrame, pd.Series, pd.Series, float]:
        """拉 K 线 + 构造 x/y timestamp。优先使用预取数据（流式流水线）。"""
        from trade_krono_cli.constraints_config import ConstraintConfig
        adjustflag = ConstraintConfig().adjustflag

        # 流式预取路径：直接返回预取数据，跳过 fetch_lookback
        if ticker in self._pre_fetched:
            df = self._pre_fetched[ticker]
            logger.debug(f"📦 Kronos 使用预取数据: {ticker} ({len(df)} 行)")
        else:
            df = fetch_lookback(
                ticker, eval_date,
                lookback=self._settings_obj.kronos_lookback,
                frequency="d",
                use_cache=self.use_cache,
                adjustflag=adjustflag,
            )
        if len(df) < self._settings_obj.kronos_lookback:
            raise RuntimeError(
                f"数据不足: {ticker} 仅 {len(df)} 行 < {self._settings_obj.kronos_lookback}"
            )

        x_df = df.iloc[-self._settings_obj.kronos_lookback:][[
            "open", "high", "low", "close", "volume", "amount"
        ]].reset_index(drop=True)
        x_ts = df.iloc[-self._settings_obj.kronos_lookback:]["timestamps"].reset_index(drop=True)
        last_close = float(x_df["close"].iloc[-1])

        # ⚠️ 预测日期从 eval_date 起算，而非 x_ts.iloc[-1]
        # 原因：如果股票在 eval_date 前停牌，x_ts.iloc[-1] 会早于 eval_date，
        #       导致 future 窗口起点早于评估日（未来函数/数据泄漏）
        future = next_business_days(eval_date, self._settings_obj.kronos_pred_len)
        future = future[:self._settings_obj.kronos_pred_len]
        y_ts = pd.Series(future, name="y_timestamp")

        return x_df, x_ts, y_ts, last_close

    # ── 结果解析 ──────────────────────────────────────────────────────────────

    def _parse_pred_df(
        self, pred_df: pd.DataFrame, last_close: float,
        sample_count: int = 1,
    ) -> dict:
        """
        从单条预测 DataFrame 解析结果（委托给 prediction_uncertainty 模块）。
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

    # ── 业务逻辑 ──────────────────────────────────────────────────────────────

    @smart_retry
    def _predict_one_retriable(self, ticker: str, eval_date: str) -> KronosForecastResult:
        """内部方法：带智能重试的 Kronos 预测（装饰器作用于此处）。"""
        return self._predict_one_impl(ticker, eval_date)

    def predict_one(self, ticker: str, eval_date: str) -> KronosForecastResult:
        ticker = validate_ticker(ticker)
        eval_date = validate_date(eval_date)
        res = KronosForecastResult(
            ticker=ticker, eval_date=eval_date,
            horizon=self._settings_obj.kronos_pred_len, interval="d",
            model_name=self.model_name,
        )
        t0 = time.time()

        if self.use_cache and self._cache:
            cached = self._cache.get_kronos(
                ticker, eval_date,
                self._settings_obj.kronos_pred_len, self.sample_count,
                config_hash=self._config_hash, model_ver=self._model_version,
            )
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
            inner_result = self._predict_one_retriable(ticker, eval_date)
            return inner_result
        except Exception as e:
            res.error = f"{type(e).__name__}: {e}"
            category, desc = classify_error(e)
            store = get_failure_store()
            store.record(ticker, eval_date, "kronos", e)
            safe_msg = sanitize_for_log(str(e))
            logger.error(f"❌ {ticker} Kronos 预测失败 [{category}]: {safe_msg}")
            return res

    def _predict_one_impl(self, ticker: str, eval_date: str) -> KronosForecastResult:
        """实际的 Kronos 预测逻辑（无重试装饰，供重试装饰器调用）。"""
        ticker = validate_ticker(ticker)
        eval_date = validate_date(eval_date)
        res = KronosForecastResult(
            ticker=ticker, eval_date=eval_date,
            horizon=self._settings_obj.kronos_pred_len, interval="d",
            model_name=self.model_name,
        )
        t0 = time.time()
        try:
            self._load()
            x_df, x_ts, y_ts, last_close = self._prepare(ticker, eval_date)
            self._run_predict(x_df, x_ts, y_ts, last_close, res)

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

    def stream_predict_one(
        self, ticker: str, eval_date: str, df: pd.DataFrame,
    ) -> KronosForecastResult:
        """
        流式预测：直接使用预取的 K 线 DataFrame，跳过缓存检查和 fetch_lookback。
        供 StreamPipeline 调用，避免每只股票重复拉取数据。
        """
        ticker = validate_ticker(ticker)
        eval_date = validate_date(eval_date)
        res = KronosForecastResult(
            ticker=ticker,
            eval_date=eval_date,
            horizon=self._settings_obj.kronos_pred_len,
            interval="d",
            model_name=self.model_name,
        )
        t0 = time.time()
        try:
            self._load()
            x_df, x_ts, y_ts, last_close = self._prepare_stream(df, ticker, eval_date)
            self._run_predict(x_df, x_ts, y_ts, last_close, res)
        except Exception as e:
            res.error = f"{type(e).__name__}: {sanitize_for_log(str(e))}"
            logger.error(f"❌ Kronos 流式预测失败 {ticker}: {res.error}")
        finally:
            res.elapsed_sec = round(time.time() - t0, 2)
        return res

    @staticmethod
    def _prepare_stream(
        df: pd.DataFrame, ticker: str, eval_date: str,
    ) -> tuple[pd.DataFrame, pd.Series, pd.Series, float]:
        """
        从预取 DataFrame 直接构造 _prepare 所需输出。
        复用 _prepare 的 slice/归一化逻辑，跳过 fetch_lookback。
        """
        lookback = len(df)
        x_df = df.iloc[-lookback:][[
            "open", "high", "low", "close", "volume", "amount"
        ]].reset_index(drop=True)
        x_ts = df.iloc[-lookback:]["timestamps"].reset_index(drop=True)
        last_close = float(x_df["close"].iloc[-1])
        from trade_krono_cli.data import next_business_days
        future = next_business_days(eval_date, 30)[:30]
        y_ts = pd.Series(future, name="y_timestamp")
        return x_df, x_ts, y_ts, last_close

    def _run_predict(
        self,
        x_df: pd.DataFrame, x_ts: pd.Series, y_ts: pd.Series,
        last_close: float, res: KronosForecastResult,
    ) -> None:
        """共享预测执行逻辑，供 predict_one 和 stream_predict_one 复用。"""
        n_samples = max(1, self.sample_count)
        adapter = self._adapter

        if n_samples > 1:
            pred_df = adapter.predict(
                df=x_df, x_timestamp=x_ts, y_timestamp=y_ts,
                pred_len=len(y_ts),
                T=self._settings_obj.kronos_T,
                top_p=self._settings_obj.kronos_top_p,
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
            pred_df = adapter.predict(
                df=x_df, x_timestamp=x_ts, y_timestamp=y_ts,
                pred_len=len(y_ts),
                T=self._settings_obj.kronos_T,
                top_p=self._settings_obj.kronos_top_p,
                sample_count=1,
            )
            avg_close = pred_df["close"].astype(float).values
            stacked = avg_close.reshape(1, -1)

        if n_samples > 1:
            from trade_krono_cli.prediction_uncertainty import (
                compute_multi_sample, build_uncertainty,
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
                change_pct=change_pct, direction=direction, vol=vol,
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

            y_ts_len = len(y_ts) if hasattr(y_ts, '__len__') else 0
            if y_ts_len == len(avg_close):
                pred_idx = y_ts.reset_index(drop=True)
            else:
                pred_idx = pd.date_range("today", periods=len(avg_close), freq="B")
            pred_df = pd.DataFrame({"close": avg_close}, index=pred_idx)

        res.forecast_dict = self._pred_df_to_dict(pred_df)

        if self._cache:
            self._cache.set_kronos(
                res.ticker, res.eval_date,
                self._settings_obj.kronos_pred_len, res.to_dict(),
                sample_count=self.sample_count,
                config_hash=self._config_hash, model_ver=self._model_version,
            )

    def predict_batch(
        self,
        tickers: list[str],
        eval_date: str,
        stop_on_error: bool = False,
    ) -> list[KronosForecastResult]:
        """
        批量预测：按 batch_size 分批推理（支持 GPU 批处理加速）。
        每批内将序列 padding 到相同长度后一次性送入模型。
        单批失败时降级为该批内逐只 predict_one。
        """
        eval_date = validate_date(eval_date)
        tickers = [validate_ticker(t) for t in tickers]
        logger.info(f"🚀 Kronos 批量预测: {len(tickers)} 只, date={eval_date}, batch_size={self.batch_size}")

        results: list[KronosForecastResult] = []
        prepared: list[tuple[str, Any, Any, Any, float] | None] = []
        all_results: list[KronosForecastResult] = []

        for tk in tickers:
            res = KronosForecastResult(
                ticker=tk, eval_date=eval_date,
                horizon=self._settings_obj.kronos_pred_len, interval="d",
                model_name=self.model_name,
            )
            if self.use_cache and self._cache:
                cached = self._cache.get_kronos(
                    tk, eval_date,
                    self._settings_obj.kronos_pred_len, self.sample_count,
                    config_hash=self._config_hash, model_ver=self._model_version,
                )
                if cached:
                    for k, v in cached.items():
                        setattr(res, k, v)
                    if isinstance(res.prediction_uncertainty, dict):
                        res.prediction_uncertainty = PredictionUncertainty.from_dict(
                            res.prediction_uncertainty
                        )
                    results.append(res)
                    prepared.append(None)
                    all_results.append(res)
                    continue

            try:
                x_df, x_ts, y_ts, last_close = self._prepare(tk, eval_date)
                prepared.append((tk, x_df, x_ts, y_ts, last_close))
                results.append(res)
                all_results.append(res)
            except DataError as e:
                res.error = f"{type(e).__name__}: {e}"
                logger.error(f"❌ 数据准备失败 {tk}: {sanitize_for_log(str(e))}")
                results.append(res)
                all_results.append(res)
            except Exception as e:
                res.error = f"{type(e).__name__}: {e}"
                safe_msg = sanitize_for_log(str(e))
                logger.error(f"❌ 数据准备异常 {tk}: {safe_msg}")
                results.append(res)
                all_results.append(res)
                prepared.append(None)
                if stop_on_error:
                    return results

        valid_items = [(p, i) for i, p in enumerate(prepared) if p is not None]
        if not valid_items:
            return results

        # ── 分批推理 ────────────────────────────────────────────────────────
        batches = self._split_batches(valid_items, self.batch_size)
        all_results: list[KronosForecastResult] = []

        for batch_idx, batch in enumerate(batches):
            batch_indices = [idx for _, idx in batch]
            df_list = [p[1] for p, _ in batch]
            x_ts_list = [p[2] for p, _ in batch]
            y_ts_list = [p[3] for p, _ in batch]
            last_closes = [p[4] for p, _ in batch]

            # 找到本批中最长序列长度，padding 所有 DataFrame
            max_seq_len = max(len(df) for df in df_list)
            padded_dfs = [self._pad_df_to_length(df, max_seq_len) for df in df_list]

            try:
                self._load()
                adapter = self._adapter
                logger.info(
                    f"⏳ 批量推理 批次 {batch_idx + 1}/{len(batches)} "
                    f"({len(batch)} 只, seq_len={max_seq_len})..."
                )
                t0 = time.time()
                pred_dfs = adapter.predict_batch(
                    df_list=padded_dfs,
                    x_timestamp_list=x_ts_list,
                    y_timestamp_list=y_ts_list,
                    pred_len=len(y_ts_list[0]),
                    T=self._settings_obj.kronos_T,
                    top_p=self._settings_obj.kronos_top_p,
                    sample_count=self.sample_count,
                )
                logger.info(f"✅ 批次 {batch_idx + 1} 完成 ({time.time()-t0:.1f}s)")
            except (DataError, ModelLoadError, RuntimeError) as e:
                logger.warning(
                    f"⚠️  批次 {batch_idx + 1} 推理失败 ({sanitize_for_log(str(e))})，"
                    f"降级为逐只推理 {len(batch)} 只"
                )
                for prepared_tuple, _ in batch:
                    tk = prepared_tuple[0]
                    all_results.append(self.predict_one(tk, eval_date))
                continue

            n_samples = max(1, self.sample_count)
            for (_, idx), pred_df, lc in zip(batch, pred_dfs, last_closes):
                res = results[idx]
                close_vals = pred_df["close"].astype(float).values
                if n_samples > 1 and close_vals.ndim == 2:
                    avg_close = close_vals.mean(axis=0)
                    stacked = close_vals
                    from trade_krono_cli.prediction_uncertainty import (
                        compute_multi_sample, build_uncertainty,
                    )
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
                        change_pct=change_pct, direction=direction,
                        vol=vol, path_dispersion=path_dispersion,
                        direction_confidence=direction_confidence,
                        confidence_score=conf_score, sample_count=n_samples,
                    )
                    res.forecast_dict = self._pred_df_to_dict(pred_df)
                else:
                    parsed = self._parse_pred_df(
                        pred_df, lc, sample_count=1
                    )
                    res.last_close = lc
                    self._apply_parsed_to_result(res, parsed)
                    res.forecast_dict = self._pred_df_to_dict(pred_df)

                if self._cache:
                    self._cache.set_kronos(
                        res.ticker, eval_date,
                        self._settings_obj.kronos_pred_len, res.to_dict(),
                        sample_count=self.sample_count,
                        config_hash=self._config_hash, model_ver=self._model_version,
                    )
                all_results.append(res)

        success = sum(1 for r in all_results if r.error is None)
        logger.info(f"📊 Kronos 批量完成: 成功 {success}/{len(all_results)}")
        return all_results

    def save_results(self, results: list[KronosForecastResult], path: str) -> str:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        data = [r.to_dict() for r in results]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"💾 Kronos 预测已保存: {path}")
        return path
