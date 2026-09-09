"""Kronos 金融时序预测封装层（业务逻辑层）。

职责边界：
  · 数据准备（fetch_lookback、x/y 构造）
  · 预测调度（单只 / 批量 + 自动降级）
  · 结果解析（_parse_pred_df、forecast_dict）
  · 缓存读写
  · 结果落盘（save_results）

资源管理（模型加载 / 设备判断 / 适配器初始化）由 KronosSession 负责。

V2.0 重构：核心逻辑拆分为 trade_krono_cli.kronos_predictor 子包。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import pandas as pd
from loguru import logger

from trade_krono_cli.config import Settings, get_settings
from trade_krono_cli.data import fetch_lookback, next_business_days
from trade_krono_cli.kronos_predictor.predictor import KronosPredictor
from trade_krono_cli.prediction_distribution import (
    PredictionDistribution,
)
from trade_krono_cli.retry_policy import (
    smart_retry,
)
from trade_krono_cli.security import (
    validate_date,
    validate_ticker,
)
from trade_krono_cli.version import compute_config_hash, get_kronos_model_version

# 向后兼容：PredictionUncertainty 是 PredictionDistribution 的别名
PredictionUncertainty = PredictionDistribution

# 向后兼容：从领域层导入并重导出，保持旧路径 importable
from trade_krono_cli.domain.kronos_result import KronosForecastResult  # noqa: E402

# 向后兼容：保持从 kronos_runner 导入 PredictionUncertainty 的能力
__all__ = ("KronosForecastResult", "KronosRunner", "PredictionUncertainty")

# Kronos 模块懒加载（保留，供旧测试兼容）
_KRONOS_IMPORTED = False


def _ensure_kronos_import(settings: Settings) -> None:
    """将 Kronos agent-harness 加入 sys.path。
    （已迁移至 adapters 层；此函数保留供旧测试兼容。）.
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


class KronosRunner:
    """生产级 Kronos 预测器（业务逻辑层，兼容接口）。

    V2.0: 内部委托给 KronosPredictor，保持向后兼容的 API。
    """

    def __init__(
        self,
        session: Any | None = None,
        no_cache: bool = False,
        sample_count: int | None = None,
        batch_size: int | None = None,
        settings: Settings | None = None,  # 向后兼容：测试中传入 mock settings
    ) -> None:
        self._session = session
        self.no_cache = no_cache
        self.sample_count = sample_count or (settings or get_settings()).kronos_sample_count
        self.batch_size = batch_size or (settings or get_settings()).kronos_batch_size

        self._settings_obj = settings or get_settings()

        # 大模型警告（向后兼容）
        self.model_name = (
            session._model_name if session else (self._settings_obj.kronos_model or "kronos-base")
        )
        if "large" in self.model_name.lower():
            logger.warning("⚠️  Kronos-large 未开源，强制切换为 Kronos-base")
            self.model_name = "kronos-base"

        self._predictor = KronosPredictor(
            settings=self._settings_obj,
            runner=self,
            use_cache=not no_cache,
            sample_count=self.sample_count,
            batch_size=self.batch_size,
        )

    @property
    def _settings(self) -> Settings:
        return self._settings_obj

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
    def _predictor_ref(self) -> Any | None:
        return getattr(self._session, "_predictor", None) if self._session else None

    @property
    def _adapter(self) -> Any:
        return self._predictor_ref if self._predictor_ref else self

    def predict(
        self,
        df: pd.DataFrame,
        x_timestamp: pd.Series,
        y_timestamp: pd.Series,
        pred_len: int,
        T: float = 1.0,
        top_p: float = 0.9,
        sample_count: int = 1,
    ) -> pd.DataFrame:
        """兼容接口：供 StreamingPredictor 和 run_predict 调用。

        在生产环境中，_adapter 指向 KronosSession 的适配器（实际模型）。
        此方法仅在无 session 的测试/兼容场景下调用。
        """
        # 此方法不应在生产中被调用；测试中应 mock 或使用有 session 的 runner
        raise RuntimeError(
            "predict() should be called via KronosSession adapter, not directly on KronosRunner"
        )

    @property
    def use_cache(self) -> bool:
        return not self.no_cache

    def _load(self) -> None:
        """加载模型（由 KronosSession 管理）。"""
        if self._session:
            self._session.load_model()

    @staticmethod
    def _pad_df_to_length(df: pd.DataFrame, target_len: int) -> pd.DataFrame:
        """将 DataFrame 填充到目标长度（静态方法，供测试直接调用）。"""
        if len(df) >= target_len:
            return df.iloc[-target_len:]
        last_row = df.iloc[[-1]]
        n_repeats = target_len - len(df)
        pad = pd.concat([last_row] * n_repeats, ignore_index=True)
        return pd.concat([df, pad], ignore_index=True).iloc[:target_len]

    @staticmethod
    def _split_batches(items: list, size: int) -> list[list]:
        """将列表分割为固定大小的批次（静态方法，供测试直接调用）。"""
        return [items[i : i + size] for i in range(0, len(items), size)]

    def _prepare(
        self, ticker: str, eval_date: str
    ) -> tuple[pd.DataFrame, pd.Series, pd.Series, float]:
        """准备预测数据。优先使用预取数据（流式模式），否则拉取历史数据。"""

        # 流式预取路径：直接返回预取数据，跳过 fetch_lookback
        if ticker in self._pre_fetched:
            df = self._pre_fetched[ticker]
            logger.debug(f"📦 Kronos 使用预取数据: {ticker} ({len(df)} 行)")
        else:
            df = fetch_lookback(
                ticker,
                eval_date,
                lookback=self._settings_obj.kronos_lookback,
                frequency="d",
                adjustflag="1",
            )

        if len(df) < self._settings_obj.kronos_lookback:
            msg = f"数据不足: {ticker} 仅 {len(df)} 行 < {self._settings_obj.kronos_lookback}"
            raise RuntimeError(msg)

        x_df = df.iloc[-self._settings_obj.kronos_lookback :][
            ["open", "high", "low", "close", "volume", "amount"]
        ].reset_index(drop=True)
        x_ts = df.iloc[-self._settings_obj.kronos_lookback :]["timestamps"].reset_index(drop=True)
        last_close = float(x_df["close"].iloc[-1])

        future = next_business_days(eval_date, self._settings_obj.kronos_pred_len)
        future = future[: self._settings_obj.kronos_pred_len]
        y_ts = pd.Series(future, name="y_timestamp")

        return x_df, x_ts, y_ts, last_close

    def _parse_pred_df(
        self, pred_df: pd.DataFrame, last_close: float, sample_count: int = 1
    ) -> dict:
        """解析预测 DataFrame。"""
        return self._predictor._result_parser.parse_pred_df(pred_df, last_close, sample_count)

    def _pred_df_to_dict(self, pred_df: pd.DataFrame) -> dict:
        """将预测 DataFrame 转为字典。"""
        return self._predictor._result_parser.pred_df_to_dict(pred_df)

    def _apply_parsed_to_result(self, res: KronosForecastResult, parsed: dict) -> None:
        """应用解析结果。"""
        self._predictor._result_parser.apply_parsed_to_result(res, parsed)

    # 向后兼容别名
    _apply_uncertainty = _apply_parsed_to_result

    @property
    def _cache(self) -> Any:
        """向后兼容：暴露内部缓存管理器（供测试 mock）。"""
        return self._predictor._cache

    @_cache.setter
    def _cache(self, value: Any) -> None:
        """允许测试 mock 缓存。"""
        self._predictor._cache = value

    @property
    def _pre_fetched(self) -> dict[str, Any]:
        """流式流水线预取数据（向后兼容）。"""
        return self._predictor._pre_fetched

    @_pre_fetched.setter
    def _pre_fetched(self, value: dict[str, Any]) -> None:
        self._predictor._pre_fetched = value

    @smart_retry
    def _predict_one_retriable(self, ticker: str, eval_date: str) -> KronosForecastResult:
        """内部方法：带智能重试的 Kronos 预测。"""
        return self._predict_one_impl(ticker, eval_date)

    def predict_one(self, ticker: str, eval_date: str) -> KronosForecastResult:
        """预测单只股票。"""
        ticker = validate_ticker(ticker)
        eval_date = validate_date(eval_date)
        res = KronosForecastResult(
            ticker=ticker,
            eval_date=eval_date,
            horizon=self._settings_obj.kronos_pred_len,
            interval="d",
            model_name=self.model_name,
        )

        if self.use_cache and self._cache:
            cached = self._cache.get_kronos(
                ticker,
                eval_date,
                self._settings_obj.kronos_pred_len,
                self.sample_count,
                config_hash=self._config_hash,
                model_ver=self._model_version,
            )
            if cached:
                logger.debug(f"📦 Kronos 缓存命中: {ticker}")
                for k, v in cached.items():
                    setattr(res, k, v)
                if isinstance(res.prediction_uncertainty, dict):
                    from trade_krono_cli.prediction_distribution import PredictionDistribution

                    res.prediction_uncertainty = PredictionDistribution.from_dict(
                        res.prediction_uncertainty,
                    )
                res.elapsed_sec = 0.0
                return res

        return self._predictor.predict_one(ticker, eval_date)  # type: ignore[misc,call-arg,return-value]  # smart_retry 装饰器使 mypy 误推断返回类型，实际运行时正常

    def _predict_one_impl(self, ticker: str, eval_date: str) -> KronosForecastResult:
        """实际的预测逻辑。"""
        return self._predictor._predict_impl(ticker, eval_date)

    def stream_predict_one(
        self, ticker: str, eval_date: str, df: pd.DataFrame
    ) -> KronosForecastResult:
        """流式预测：直接使用预取的 K 线 DataFrame。"""
        from trade_krono_cli.kronos_predictor.streaming import StreamingPredictor

        streamer = StreamingPredictor(self._settings_obj, self)
        return streamer.predict(ticker, eval_date, df)

    def _prepare_stream(
        self, df: pd.DataFrame, ticker: str, eval_date: str
    ) -> tuple[pd.DataFrame, pd.Series, pd.Series, float]:
        """从预取 DataFrame 直接构造预测数据。"""
        from trade_krono_cli.kronos_predictor.streaming import StreamingPredictor

        streamer = StreamingPredictor(self._settings_obj, self)
        return streamer._prepare_stream(df, ticker, eval_date)

    def _run_predict(
        self,
        x_df: pd.DataFrame,
        x_ts: pd.Series,
        y_ts: pd.Series,
        last_close: float,
        res: KronosForecastResult,
    ) -> None:
        """执行单次预测。"""
        self._predictor._result_parser.run_predict(self, x_df, x_ts, y_ts, last_close, res)

    def predict_batch(
        self,
        tickers: list[str],
        eval_date: str,
        stop_on_error: bool = False,
    ) -> list[KronosForecastResult]:
        """批量预测。"""
        # 向后兼容：先检查缓存
        results: list[KronosForecastResult] = []
        all_cached = True
        for tk in tickers:
            if self.use_cache and self._cache:
                cached = self._cache.get_kronos(
                    tk,
                    eval_date,
                    self._settings_obj.kronos_pred_len,
                    self.sample_count,
                    config_hash=self._config_hash,
                    model_ver=self._model_version,
                )
                if cached:
                    res = KronosForecastResult(
                        ticker=tk,
                        eval_date=eval_date,
                        horizon=self._settings_obj.kronos_pred_len,
                        interval="d",
                        model_name=self.model_name,
                    )
                    for k, v in cached.items():
                        setattr(res, k, v)
                    if isinstance(res.prediction_uncertainty, dict):
                        from trade_krono_cli.prediction_distribution import PredictionDistribution

                        res.prediction_uncertainty = PredictionDistribution.from_dict(
                            res.prediction_uncertainty
                        )
                    res.elapsed_sec = 0.0
                    results.append(res)
                    continue
            all_cached = False
            break

        if all_cached:
            return results

        return self._predictor.predict_batch(tickers, eval_date, stop_on_error)

    def save_results(self, results: list[KronosForecastResult], path: str) -> str:
        """保存预测结果到文件。"""
        path_obj = Path(path)
        path_obj.parent.mkdir(parents=True, exist_ok=True)

        records = []
        for r in results:
            d = r.to_dict()
            if r.forecast_dict:
                d["forecast_dict"] = {
                    "timestamps": r.forecast_dict.get("timestamps", [])[:5],
                    "close": r.forecast_dict.get("close", [])[:5],
                    "note": f"截断显示，共 {len(r.forecast_dict.get('close', []))} 个预测点",
                }
            records.append(d)

        output = [
            {"project": "trade-krono-cli", "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S")},
            *records,
        ]

        with open(path_obj, "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)

        logger.info(f"💾 Kronos 结果已保存: {path}")
        return path
