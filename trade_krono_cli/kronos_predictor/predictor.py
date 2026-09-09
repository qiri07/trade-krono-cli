"""核心预测执行模块。

包含 predict_one, predict_batch 等主入口方法。
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from loguru import logger

from trade_krono_cli.errors import DataError, ModelLoadError
from trade_krono_cli.kronos_predictor.cache import KronosCacheManager
from trade_krono_cli.kronos_predictor.data_prep import DataPreparator
from trade_krono_cli.kronos_predictor.result_parser import ResultParser
from trade_krono_cli.retry_policy import classify_error, get_failure_store, smart_retry
from trade_krono_cli.security import sanitize_for_log, validate_date, validate_ticker
from trade_krono_cli.version import compute_config_hash, get_kronos_model_version

if TYPE_CHECKING:
    from trade_krono_cli.config import Settings
    from trade_krono_cli.domain.kronos_result import KronosForecastResult


class KronosPredictor:
    """Kronos 预测执行器（核心逻辑）。

    负责：
    - 单只预测调度（带缓存和重试）
    - 批量预测调度（分批推理 + 自动降级）
    - 预测结果缓存
    """

    def __init__(
        self,
        settings: "Settings",
        runner: Any,
        use_cache: bool = True,
        sample_count: int | None = None,
        batch_size: int | None = None,
    ) -> None:
        self._settings = settings
        self._runner = runner
        self.use_cache = use_cache
        self.sample_count = sample_count or settings.kronos_sample_count
        self.batch_size = batch_size or settings.kronos_batch_size

        self._cache = KronosCacheManager(settings)
        self._data_prep = DataPreparator(settings)
        self._result_parser = ResultParser(settings)
        # 流式流水线预取（向后兼容）
        self._pre_fetched: dict[str, Any] = {}

        self._config_hash = compute_config_hash(settings)
        self._model_version = get_kronos_model_version(
            settings.kronos_model or "kronos-base",
            settings.kronos_tokenizer,
            "cpu",
        )

    @property
    def model_name(self) -> str:
        return getattr(self._runner, "model_name", "kronos")

    @smart_retry
    def predict_one(self, ticker: str, eval_date: str) -> "KronosForecastResult":
        """预测单只股票，带缓存和重试。"""
        ticker = validate_ticker(ticker)
        eval_date = validate_date(eval_date)

        res = self._result_parser.create_empty_result(
            ticker=ticker,
            eval_date=eval_date,
            horizon=self._settings.kronos_pred_len,
            model_name=self.model_name,
        )

        # 检查缓存
        if self.use_cache and self._cache:
            cached = self._cache.get(
                ticker,
                eval_date,
                self._settings.kronos_pred_len,
                self.sample_count,
                config_hash=self._config_hash,
                model_ver=self._model_version,
            )
            if cached:
                logger.debug(f"📦 Kronos 缓存命中: {ticker}")
                res = self._result_parser.apply_cached(res, cached)
                return res

        try:
            return self._predict_impl(ticker, eval_date)
        except Exception as e:
            res.error = f"{type(e).__name__}: {e}"
            category, _desc = classify_error(e)
            store = get_failure_store()
            store.record(ticker, eval_date, "kronos", e)
            safe_msg = sanitize_for_log(str(e))
            logger.error(f"❌ {ticker} Kronos 预测失败 [{category}]: {safe_msg}")
            return res

    def _predict_impl(self, ticker: str, eval_date: str) -> "KronosForecastResult":
        """实际预测逻辑（无重试）。"""
        ticker = validate_ticker(ticker)
        eval_date = validate_date(eval_date)
        res = self._result_parser.create_empty_result(
            ticker=ticker,
            eval_date=eval_date,
            horizon=self._settings.kronos_pred_len,
            model_name=self.model_name,
        )

        t0 = time.time()
        try:
            self._runner._load()  # type: ignore
            x_df, x_ts, y_ts, last_close = self._data_prep.prepare(ticker, eval_date)
            self._result_parser.run_predict(self._runner, x_df, x_ts, y_ts, last_close, res)
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

        # 写入缓存
        if self._cache:
            self._cache.set(res.ticker, res.eval_date, res.to_dict())

        return res

    def predict_batch(
        self,
        tickers: list[str],
        eval_date: str,
        stop_on_error: bool = False,
    ) -> list["KronosForecastResult"]:
        """批量预测：按 batch_size 分批推理。"""
        eval_date = validate_date(eval_date)
        tickers = [validate_ticker(t) for t in tickers]
        logger.info(
            f"🚀 Kronos 批量预测: {len(tickers)} 只, date={eval_date}, batch_size={self.batch_size}"
        )

        results: list["KronosForecastResult"] = []
        prepared: list[tuple[str, Any, Any, Any, float] | None] = []

        for tk in tickers:
            res = self._result_parser.create_empty_result(
                ticker=tk,
                eval_date=eval_date,
                horizon=self._settings.kronos_pred_len,
                model_name=self.model_name,
            )

            # 检查缓存
            if self.use_cache and self._cache:
                cached = self._cache.get(
                    tk,
                    eval_date,
                    self._settings.kronos_pred_len,
                    self.sample_count,
                    config_hash=self._config_hash,
                    model_ver=self._model_version,
                )
                if cached:
                    res = self._result_parser.apply_cached(res, cached)
                    results.append(res)
                    prepared.append(None)
                    continue

            try:
                x_df, x_ts, y_ts, last_close = self._data_prep.prepare(tk, eval_date)
                prepared.append((tk, x_df, x_ts, y_ts, last_close))
                results.append(res)
            except DataError as e:
                res.error = f"{type(e).__name__}: {e}"
                logger.error(f"❌ 数据准备失败 {tk}: {sanitize_for_log(str(e))}")
                results.append(res)
                prepared.append(None)
                if stop_on_error:
                    return results
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

        # 分批推理
        batches = self._data_prep.split_batches(valid_items, self.batch_size)

        for batch_idx, batch in enumerate(batches):
            df_list = [p[1] for p, _ in batch]
            x_ts_list = [p[2] for p, _ in batch]
            y_ts_list = [p[3] for p, _ in batch]
            idx_list = [i for _, i in batch]

            try:
                pred_dfs = self._runner._adapter.predict(  # type: ignore
                    df=df_list,
                    x_timestamp=x_ts_list,
                    y_timestamp=y_ts_list,
                    pred_len=len(y_ts_list[0]) if y_ts_list else 0,
                    T=self._settings.kronos_T,
                    top_p=self._settings.kronos_top_p,
                    sample_count=self.sample_count,
                )
                for pred_df, idx in zip(pred_dfs, idx_list):
                    tk, _, _, _, last_close = prepared[idx]
                    results[idx] = self._result_parser.parse_batch_result(
                        results[idx], pred_df, last_close, self.sample_count
                    )
                    if self._cache:
                        self._cache.set(
                            results[idx].ticker,
                            results[idx].eval_date,
                            results[idx].to_dict(),
                        )
            except Exception as e:
                logger.warning(f"批次 {batch_idx} 推理失败，降级逐只预测: {e}")
                for pred_df, idx in zip(pred_dfs, idx_list):
                    tk, _, _, _, last_close = prepared[idx]
                    results[idx] = self.predict_one(tk, eval_date)  # type: ignore[misc,call-arg,assignment]  # smart_retry 装饰器使 mypy 误推断返回类型，实际运行时正常

        return results
