"""
Kronos 金融时序预测封装层：
  • 模型懒加载 + 显存友好
  • 批量 predict（GPU/CPU 自动切换）
  • 结果结构化 + 缓存
  • 集成 data.py 拉 K 线
  • 预测不确定性量化模块：
      expected_return / direction / volatility /
      path_dispersion / confidence / stability
"""
from __future__ import annotations

import time
import json
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Optional, Any

import numpy as np
import pandas as pd

from loguru import logger
from trade_krono_cli.config import get_settings
from trade_krono_cli.security import (
    validate_ticker,
    validate_date,
    retry,
    sanitize_for_log,
    ensure_import_path,
)
from trade_krono_cli.cache import get_cache
from trade_krono_cli.data import fetch_lookback, next_business_days

# Kronos 模块懒加载
_KRONOS_IMPORTED = False


def _ensure_kronos_import() -> None:
    global _KRONOS_IMPORTED
    if _KRONOS_IMPORTED:
        return
    s = get_settings()
    # 优先注入 agent-harness（包含 cli_anything.kronos）
    harness_root = s.kronos_root / "agent-harness"
    kronos_root = s.kronos_root
    ensure_import_path(harness_root, kronos_root)
    _KRONOS_IMPORTED = True
    logger.debug(f"Kronos 路径已加入: {harness_root} + {kronos_root}")


# ── 预测不确定性量化子模块 ───────────────────────────────────────────────────

@dataclass
class PredictionUncertainty:
    """
    预测不确定性量化结果。

    字段语义：
      expected_return       预期收益率（%），= (final_close - last_close) / last_close * 100
      direction             UP / DOWN / FLAT（阈值 ±1%）
      direction_confidence  方向置信度 0-1，基于 |change_pct| 与波动率的比率
                            = sigmoid(|change_pct| / (10 * std + 1e-8))
      volatility            预测路径的标准差（绝对价格波动）
      path_dispersion       归一化路径分散度；多样本时为 std/|mean|，单样本时为 None
      confidence_score      综合不确定性评分 0-100
                            多样本：direction_confidence*50 + max(0, 50-dispersion*200)
                            单样本：direction_confidence * 100
      sample_count_used     实际使用的样本数
    """
    expected_return: Optional[float] = None
    direction: Optional[str] = None
    direction_confidence: Optional[float] = None
    volatility: Optional[float] = None
    path_dispersion: Optional[float] = None
    confidence_score: Optional[float] = None
    sample_count_used: int = 1

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "PredictionUncertainty":
        """从 dict 反序列化。"""
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# ── 预测结果 ─────────────────────────────────────────────────────────────────

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

    # 新增：预测不确定性量化模块
    prediction_uncertainty: Optional[PredictionUncertainty] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        if self.prediction_uncertainty is not None:
            d["prediction_uncertainty"] = self.prediction_uncertainty.to_dict()
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
    - 预测不确定性量化模块
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
        self.use_cache = use_cache and not no_cache
        self.use_sample_confidence = s.kronos_use_sample_confidence

        self._cache = get_cache()
        self._predictor: Any = None
        self._device: str = "cpu"
        self._max_context = 512

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
        """懒加载 Kronos 模型（通过 cli_anything.kronos）。"""
        if self._predictor is not None:
            return

        _ensure_kronos_import()
        device = self._resolve_device()
        self._device = device

        logger.info(f"⏳ 加载 Kronos 模型（首次约 1-3 分钟）...")
        t0 = time.time()

        try:
            from cli_anything.kronos.utils.kronos_backend import load_model

            predictor, meta = load_model(
                name=self.model_name.lower(),
                device=device,
            )
            self._predictor = predictor
            self._max_context = meta.get("max_context", 512)
            logger.info(
                f"✅ Kronos 模型加载完成 ({time.time()-t0:.1f}s, device={device})"
            )

        except ImportError as e:
            raise RuntimeError(
                f"无法导入 cli_anything.kronos：{e}。"
                f"请确认已安装 Kronos agent-harness "
                f"（pip install -e {self._settings.kronos_root / 'agent-harness'}）"
            ) from e

    @property
    def _settings(self):
        return get_settings()

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
        从单条预测 DataFrame 解析结果。

        Parameters
        ----------
        pred_df : 预测结果 DataFrame
        last_close : 历史最后一个收盘价
        sample_count : 实际样本数（1=单路径，>1=多路径均值）
        """
        closes = pred_df["close"].astype(float).values
        if len(closes) == 0:
            raise RuntimeError("Kronos 返回空预测")

        final_close = float(closes[-1])
        mean_close = float(np.mean(closes))
        change_pct = (final_close - last_close) / last_close * 100.0
        direction = "UP" if change_pct > 1.0 else ("DOWN" if change_pct < -1.0 else "FLAT")
        vol = float(np.std(closes))

        # 传统 confidence_band（仅向后兼容）
        q_low = float(np.percentile(closes, 25)) if len(closes) > 1 else mean_close
        q_high = float(np.percentile(closes, 75)) if len(closes) > 1 else mean_close

        # direction_confidence: sigmoid(|change_pct| / (10*std + eps))
        denom = 10.0 * vol + 1e-8
        raw_ratio = abs(change_pct) / denom
        direction_confidence = float(1.0 / (1.0 + np.exp(-raw_ratio)))

        # path_dispersion：多样本才有跨路径统计意义
        if sample_count > 1:
            if abs(mean_close) > 1e-8:
                path_dispersion = vol / abs(mean_close)
            else:
                path_dispersion = 0.0
        else:
            path_dispersion = None  # 单样本无跨路径方差

        # confidence_score
        if path_dispersion is not None:
            score = direction_confidence * 50.0 + max(0.0, 50.0 - path_dispersion * 200.0)
        else:
            score = direction_confidence * 100.0
        confidence_score = round(min(100.0, max(0.0, score)), 2)

        uncertainty = PredictionUncertainty(
            expected_return=round(change_pct, 3),
            direction=direction,
            direction_confidence=round(direction_confidence, 4),
            volatility=round(vol, 4),
            path_dispersion=round(path_dispersion, 6) if path_dispersion is not None else None,
            confidence_score=confidence_score,
            sample_count_used=sample_count,
        )

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
            "prediction_uncertainty": uncertainty.to_dict(),
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

    def _apply_uncertainty(self, res: KronosForecastResult, parsed: dict) -> None:
        """将 parsed dict 写入 result，单独处理 prediction_uncertainty。"""
        pu_dict = parsed.pop("prediction_uncertainty", None)
        for k, v in parsed.items():
            setattr(res, k, v)
        if pu_dict:
            res.prediction_uncertainty = PredictionUncertainty(**pu_dict)

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
                # 缓存中 prediction_uncertainty 是 dict，需还原为对象
                if isinstance(res.prediction_uncertainty, dict):
                    res.prediction_uncertainty = PredictionUncertainty.from_dict(res.prediction_uncertainty)
                res.elapsed_sec = 0.0
                return res

        try:
            self._load()
            x_df, x_ts, y_ts, last_close = self._prepare(ticker, eval_date)

            n_samples = max(1, self.sample_count)
            assert self._predictor is not None

            if n_samples > 1:
                # 多样本：直接委托模型内部处理，避免 N 次独立推理
                pred_df = self._predictor.predict(
                    df=x_df, x_timestamp=x_ts, y_timestamp=y_ts,
                    pred_len=len(y_ts), T=self.T, top_p=self.top_p,
                    sample_count=n_samples, verbose=False,
                )
                # 模型返回多路径时，close 列是 (n_samples, pred_len) 的堆叠
                # 需按列取均值作为单条路径
                close_vals = pred_df["close"].astype(float).values
                if close_vals.ndim == 2:
                    avg_close = close_vals.mean(axis=0)
                    stacked = close_vals  # 保留原始路径供后续分析
                else:
                    avg_close = close_vals
                    stacked = close_vals.reshape(1, -1)
            else:
                # 单样本：直接一次调用
                pred_df = self._predictor.predict(
                    df=x_df, x_timestamp=x_ts, y_timestamp=y_ts,
                    pred_len=len(y_ts), T=self.T, top_p=self.top_p,
                    sample_count=1, verbose=False,
                )
                avg_close = pred_df["close"].astype(float).values
                stacked = avg_close.reshape(1, -1)

            # 计算预测结果（单样本或多样本统一处理）
            if n_samples > 1:
                final_close = float(avg_close[-1])
                mean_close = float(np.mean(avg_close))
                change_pct = (final_close - last_close) / last_close * 100.0
                direction = "UP" if change_pct > 1.0 else ("DOWN" if change_pct < -1.0 else "FLAT")
                vol = float(np.std(avg_close))

                # 跨样本最终价的变异系数 → 真正的路径间不确定性
                sample_std = float(np.std(stacked[:, -1]))
                sample_cv = sample_std / abs(final_close) if abs(final_close) > 1e-8 else 0.0

                raw_ratio = abs(change_pct) / (10.0 * sample_std + 1e-8)
                direction_confidence = float(1.0 / (1.0 + np.exp(-raw_ratio)))

                conf_score = direction_confidence * 50.0 + max(0.0, 50.0 - sample_cv * 200.0)
                conf_score = round(min(100.0, max(0.0, conf_score)), 2)

                uncertainty = PredictionUncertainty(
                    expected_return=round(change_pct, 3),
                    direction=direction,
                    direction_confidence=round(direction_confidence, 4),
                    volatility=round(vol, 4),
                    path_dispersion=round(sample_cv, 6),
                    confidence_score=conf_score,
                    sample_count_used=n_samples,
                )

                res.predicted_close_mean = round(mean_close, 4)
                res.predicted_close_final = round(final_close, 4)
                res.expected_change_pct = round(change_pct, 3)
                res.direction = direction
                res.volatility_proxy = round(vol, 4)
                res.confidence_band = {
                    "low": round(float(np.percentile(avg_close, 25)), 4),
                    "high": round(float(np.percentile(avg_close, 75)), 4),
                }
                res.prediction_uncertainty = uncertainty
            else:
                # 单样本：退化为方向置信度
                parsed = self._parse_pred_df(
                    pd.DataFrame({"close": avg_close}), last_close, sample_count=1
                )
                res.last_close = last_close
                self._apply_uncertainty(res, parsed)

                # 重建预测 DataFrame（使用均值），供 forecast_dict 使用
                # ⚠️ 使用 y_ts 的实际日期，不用 "today"（避免日期漂移）
                # 防御性：若 y_ts 长度不匹配 avg_close（如测试 mock 场景），回退到基于 pred_len 的日期范围
                y_ts_len = len(y_ts) if hasattr(y_ts, '__len__') else 0
                if y_ts_len == len(avg_close):
                    pred_idx = y_ts.reset_index(drop=True)
                else:
                    pred_idx = pd.date_range("today", periods=len(avg_close), freq="B")
                pred_df = pd.DataFrame(
                    {"close": avg_close},
                    index=pred_idx,
                )

            res.forecast_dict = self._pred_df_to_dict(pred_df)

            if self._cache:
                self._cache.set_kronos(
                    ticker, eval_date, self.pred_len, res.to_dict(),
                    sample_count=self.sample_count,
                )

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
                    # 缓存中 prediction_uncertainty 是 dict，需还原为对象
                    if isinstance(res.prediction_uncertainty, dict):
                        res.prediction_uncertainty = PredictionUncertainty.from_dict(res.prediction_uncertainty)
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

            assert self._predictor is not None
            pred_dfs = self._predictor.predict_batch(
                df_list=df_list,
                x_timestamp_list=x_ts_list,
                y_timestamp_list=y_ts_list,
                pred_len=len(y_ts_list[0]),
                T=self.T,
                top_p=self.top_p,
                sample_count=self.sample_count,
                verbose=False,
            )
            logger.info(f"✅ 批量推理完成 ({time.time()-t0:.1f}s)")

            n_samples = max(1, self.sample_count)
            for (_, idx), pred_df, lc in zip(valid_items, pred_dfs, last_closes):
                res = results[idx]
                close_vals = pred_df["close"].astype(float).values
                if n_samples > 1 and close_vals.ndim == 2:
                    avg_close = close_vals.mean(axis=0)
                    stacked = close_vals
                    final_close = float(avg_close[-1])
                    mean_close = float(np.mean(avg_close))
                    change_pct = (final_close - lc) / lc * 100.0
                    direction = "UP" if change_pct > 1.0 else ("DOWN" if change_pct < -1.0 else "FLAT")
                    vol = float(np.std(avg_close))
                    sample_std = float(np.std(stacked[:, -1]))
                    sample_cv = sample_std / abs(final_close) if abs(final_close) > 1e-8 else 0.0
                    raw_ratio = abs(change_pct) / (10.0 * sample_std + 1e-8)
                    direction_confidence = float(1.0 / (1.0 + np.exp(-raw_ratio)))
                    conf_score = direction_confidence * 50.0 + max(0.0, 50.0 - sample_cv * 200.0)
                    conf_score = round(min(100.0, max(0.0, conf_score)), 2)
                    uncertainty = PredictionUncertainty(
                        expected_return=round(change_pct, 3),
                        direction=direction,
                        direction_confidence=round(direction_confidence, 4),
                        volatility=round(vol, 4),
                        path_dispersion=round(sample_cv, 6),
                        confidence_score=conf_score,
                        sample_count_used=n_samples,
                    )
                    res.predicted_close_mean = round(mean_close, 4)
                    res.predicted_close_final = round(final_close, 4)
                    res.expected_change_pct = round(change_pct, 3)
                    res.direction = direction
                    res.volatility_proxy = round(vol, 4)
                    res.confidence_band = {
                        "low": round(float(np.percentile(avg_close, 25)), 4),
                        "high": round(float(np.percentile(avg_close, 75)), 4),
                    }
                    res.prediction_uncertainty = uncertainty
                    res.forecast_dict = self._pred_df_to_dict(pred_df)
                else:
                    parsed = self._parse_pred_df(pred_df, lc, sample_count=1)
                    res.last_close = lc
                    self._apply_uncertainty(res, parsed)
                    res.forecast_dict = self._pred_df_to_dict(pred_df)
                if self._cache:
                    self._cache.set_kronos(
                        res.ticker, eval_date, self.pred_len, res.to_dict(),
                        sample_count=self.sample_count,
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
