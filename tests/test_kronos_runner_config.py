"""测试 PredictionUncertainty / KronosForecastResult 数据类。"""

from unittest.mock import MagicMock, patch

import pandas as pd
import pytest


class TestKronosRunnerResolveDevice:
    """_resolve_device 测试（由 KronosSession 负责）。"""

    def test_cpu_device(self) -> None:
        from trade_krono_cli.models.kronos_session import KronosSession

        with patch("trade_krono_cli.models.kronos_session.KronosRunner"):
            session = KronosSession(device="cpu")
        assert session._resolve_device() == "cpu"

    def test_cuda_device_no_torch(self) -> None:
        """无 torch 时 cuda 回退到 cpu。"""
        from trade_krono_cli.models.kronos_session import KronosSession

        with patch("trade_krono_cli.models.kronos_session.KronosRunner"):
            session = KronosSession(device="cuda")
        with patch.dict("sys.modules", {"torch": None}):
            result = session._resolve_device()
        assert result == "cpu"

    def test_cuda_with_torch_not_available(self) -> None:
        """torch.cuda.is_available() 为 False 时回退到 cpu。"""
        from trade_krono_cli.models.kronos_session import KronosSession

        with patch("trade_krono_cli.models.kronos_session.KronosRunner"):
            session = KronosSession(device="cuda")
        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = False
        with patch.dict("sys.modules", {"torch": mock_torch}):
            result = session._resolve_device()
        assert result == "cpu"

    def test_cuda_with_torch_available(self) -> None:
        """torch.cuda.is_available() 为 True 时返回 cuda。"""
        from trade_krono_cli.models.kronos_session import KronosSession

        with patch("trade_krono_cli.models.kronos_session.KronosRunner"):
            session = KronosSession(device="cuda")
        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = True
        with patch.dict("sys.modules", {"torch": mock_torch}):
            result = session._resolve_device()
        assert result == "cuda"

    def test_large_model_warning(self) -> None:
        """Large 模型名应触发警告并切换为 base。"""
        from tests.conftest import make_mock_settings
        from trade_krono_cli.kronos_runner import KronosRunner

        with patch("trade_krono_cli.kronos_runner.logger.warning"):
            settings = make_mock_settings(
                kronos_model="kronos-large",
                kronos_tokenizer="kronos-base",
                kronos_device="cpu",
                kronos_lookback=400,
                kronos_pred_len=30,
                kronos_sample_count=1,
                kronos_T=1.0,
                kronos_top_p=0.9,
                kronos_use_sample_confidence=False,
            )
            runner = KronosRunner(no_cache=True, settings=settings)
        assert runner.model_name == "kronos-base"

    def test_prepare_uses_eval_date_not_last_data_date(self) -> None:
        """_prepare 的 future 日期应从 eval_date 起算，而非数据末尾日期。"""
        from tests.conftest import make_mock_settings
        from trade_krono_cli.kronos_runner import KronosRunner

        settings = make_mock_settings(
            kronos_model="kronos-base",
            kronos_tokenizer="kronos-base",
            kronos_device="cpu",
            kronos_lookback=5,
            kronos_pred_len=3,
            kronos_sample_count=1,
            kronos_T=1.0,
            kronos_top_p=0.9,
            kronos_use_sample_confidence=False,
        )
        runner = KronosRunner(no_cache=True, settings=settings)

        mock_df = pd.DataFrame(
            {
                "timestamps": pd.to_datetime(
                    [
                        "2026-07-27",
                        "2026-07-28",
                        "2026-07-29",
                        "2026-07-30",
                        "2026-07-31",
                    ],
                ),
                "open": [100.0] * 5,
                "high": [101.0] * 5,
                "low": [99.0] * 5,
                "close": [100.0] * 5,
                "volume": [1e6] * 5,
                "amount": [1e8] * 5,
            },
        )
        with patch("trade_krono_cli.kronos_runner.fetch_lookback", return_value=mock_df):
            _x_df, _x_ts, y_ts, last_close = runner._prepare("sh.600519", "2026-08-11")

        assert str(y_ts.iloc[0]) == "2026-08-12 00:00:00"
        assert len(y_ts) == 3
        assert last_close == 100.0

    def test_prepare_raises_on_suspended_stock(self) -> None:
        """当 fetch_lookback 抛出数据过旧异常（停牌超过阈值），
        _prepare 应将异常传播出去，阻止预测。
        """
        from tests.conftest import make_mock_settings
        from trade_krono_cli.kronos_runner import KronosRunner

        settings = make_mock_settings(
            kronos_model="kronos-base",
            kronos_tokenizer="kronos-base",
            kronos_device="cpu",
            kronos_lookback=5,
            kronos_pred_len=3,
            kronos_sample_count=1,
            kronos_T=1.0,
            kronos_top_p=0.9,
            kronos_use_sample_confidence=False,
        )
        runner = KronosRunner(no_cache=True, settings=settings)

        with patch("trade_krono_cli.kronos_runner.fetch_lookback") as mock_fetch:
            mock_fetch.side_effect = RuntimeError(
                "数据过旧: sh.600519 最后交易日 2026-06-05 与评估日 2026-08-11 "
                "相差 34 个交易日（阈值 10），疑似停牌或退市",
            )
            with pytest.raises(RuntimeError, match="数据过旧|疑似停牌"):
                runner._prepare("sh.600519", "2026-08-11")


class TestKronosRunnerSaveResults:
    """save_results 测试。"""

    def test_saves_json(self, tmp_path) -> None:
        from tests.conftest import make_mock_settings
        from trade_krono_cli.kronos_runner import KronosForecastResult, KronosRunner

        settings = make_mock_settings(
            kronos_model="kronos-base",
            kronos_tokenizer="kronos-base",
            kronos_device="cpu",
            kronos_lookback=400,
            kronos_pred_len=30,
            kronos_sample_count=1,
            kronos_T=1.0,
            kronos_top_p=0.9,
            kronos_use_sample_confidence=False,
        )
        runner = KronosRunner(no_cache=True, settings=settings)

        results = [
            KronosForecastResult(
                ticker="sh.600519",
                eval_date="2026-08-12",
                horizon=30,
                direction="UP",
                expected_change_pct=2.0,
            ),
            KronosForecastResult(
                ticker="sz.000858",
                eval_date="2026-08-12",
                horizon=30,
                direction="DOWN",
                expected_change_pct=-1.5,
            ),
        ]
        path = str(tmp_path / "kronos_out.json")
        returned = runner.save_results(results, path)
        assert returned == path
        import json

        with open(path) as f:
            data = json.load(f)
        # 新项目格式：顶层含 project 字段，结果在 indices 1..N
        assert data[0].get("project") == "trade-krono-cli"
        results = data[1:]
        assert len(results) == 2
        assert results[0]["ticker"] == "sh.600519"
