"""测试 TradingAgentsAdapterImpl — 生命周期与接口实现。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from trade_krono_cli.adapters.tradingagents import TradingAgentsAdapterImpl
from trade_krono_cli.errors import ModelLoadError


class TestTradingAgentsAdapterImpl:
    """TradingAgentsAdapterImpl 测试。"""

    def test_init_state(self) -> None:
        adapter = TradingAgentsAdapterImpl()
        assert adapter._run_analysis is None
        assert adapter._build_config is None

    def test_load_success(self) -> None:
        mock_run = MagicMock(return_value={"signal": "BUY"})
        mock_build = MagicMock(return_value={})

        settings = MagicMock()
        settings.tradingagents_root = Path("/fake/path")

        with patch("trade_krono_cli.adapters.tradingagents.ensure_import_path"):
            with patch.dict(
                "sys.modules",
                {
                    "cli_anything": MagicMock(),
                    "cli_anything.tradingagents": MagicMock(),
                    "cli_anything.tradingagents.core": MagicMock(),
                    "cli_anything.tradingagents.core.analysis": MagicMock(),
                },
            ):
                sys_modules = __import__("sys").modules
                sys_modules["cli_anything.tradingagents.core.analysis"].run_analysis = mock_run
                sys_modules["cli_anything.tradingagents.core.analysis"].build_config = mock_build

                adapter = TradingAgentsAdapterImpl()
                adapter.load(settings)

        assert adapter._run_analysis is not None
        assert adapter._build_config is not None

    def test_load_already_loaded_skips(self) -> None:
        adapter = TradingAgentsAdapterImpl()
        adapter._run_analysis = MagicMock()
        adapter._build_config = MagicMock()

        settings = MagicMock()
        settings.tradingagents_root = Path("/fake/path")

        with patch("trade_krono_cli.adapters.tradingagents.ensure_import_path") as mock_ensure:
            adapter.load(settings)
            mock_ensure.assert_not_called()

    def test_load_import_error_raises(self) -> None:
        settings = MagicMock()
        settings.tradingagents_root = Path("/fake/path")

        with patch("trade_krono_cli.adapters.tradingagents.ensure_import_path"):
            with patch.dict(
                "sys.modules",
                {
                    "cli_anything": MagicMock(),
                    "cli_anything.tradingagents": MagicMock(),
                    "cli_anything.tradingagents.core": MagicMock(),
                    "cli_anything.tradingagents.core.analysis": None,
                },
            ):
                import sys

                sys.modules["cli_anything.tradingagents.core.analysis"] = None
                adapter = TradingAgentsAdapterImpl()
                with pytest.raises(ModelLoadError):
                    adapter.load(settings)

    def test_build_config_not_loaded_raises(self) -> None:
        adapter = TradingAgentsAdapterImpl()
        with pytest.raises(RuntimeError, match="尚未加载"):
            adapter.build_config(foo="bar")

    def test_run_analysis_not_loaded_raises(self) -> None:
        adapter = TradingAgentsAdapterImpl()
        with pytest.raises(RuntimeError, match="尚未加载"):
            adapter.run_analysis("sh.600519", {})

    def test_build_config_delegates(self) -> None:
        mock_build = MagicMock(return_value={"trade_date": "2026-09-01"})
        settings = MagicMock()
        settings.tradingagents_root = Path("/fake/path")

        adapter = TradingAgentsAdapterImpl()
        with patch("trade_krono_cli.adapters.tradingagents.ensure_import_path"):
            with patch.dict(
                "sys.modules",
                {
                    "cli_anything": MagicMock(),
                    "cli_anything.tradingagents": MagicMock(),
                    "cli_anything.tradingagents.core": MagicMock(),
                    "cli_anything.tradingagents.core.analysis": MagicMock(),
                },
            ):
                import sys

                sys.modules["cli_anything.tradingagents.core.analysis"].build_config = mock_build
                adapter.load(settings)

        result = adapter.build_config(foo="bar")
        mock_build.assert_called_once_with(foo="bar")
        assert result == {"trade_date": "2026-09-01"}

    def test_run_analysis_delegates(self) -> None:
        mock_run = MagicMock(return_value={"signal": "BUY", "confidence": 80.0})
        settings = MagicMock()
        settings.tradingagents_root = Path("/fake/path")

        adapter = TradingAgentsAdapterImpl()
        with patch("trade_krono_cli.adapters.tradingagents.ensure_import_path"):
            with patch.dict(
                "sys.modules",
                {
                    "cli_anything": MagicMock(),
                    "cli_anything.tradingagents": MagicMock(),
                    "cli_anything.tradingagents.core": MagicMock(),
                    "cli_anything.tradingagents.core.analysis": MagicMock(),
                },
            ):
                import sys

                sys.modules["cli_anything.tradingagents.core.analysis"].run_analysis = mock_run
                adapter.load(settings)

        result = adapter.run_analysis("sh.600519", {"trade_date": "2026-09-01"})
        mock_run.assert_called_once()
        assert result["signal"] == "BUY"

    def test_run_analysis_with_extra_kwargs(self) -> None:
        mock_run = MagicMock(return_value={})
        settings = MagicMock()
        settings.tradingagents_root = Path("/fake/path")

        adapter = TradingAgentsAdapterImpl()
        with patch("trade_krono_cli.adapters.tradingagents.ensure_import_path"):
            with patch.dict(
                "sys.modules",
                {
                    "cli_anything": MagicMock(),
                    "cli_anything.tradingagents": MagicMock(),
                    "cli_anything.tradingagents.core": MagicMock(),
                    "cli_anything.tradingagents.core.analysis": MagicMock(),
                },
            ):
                import sys

                sys.modules["cli_anything.tradingagents.core.analysis"].run_analysis = mock_run
                adapter.load(settings)

        config = {"trade_date": "2026-09-01", "extra_kwargs": {"analysts": ["tech", "fund"]}}
        adapter.run_analysis("sh.600519", config)
        call_args = mock_run.call_args
        assert call_args[0][0] == "sh.600519"
        assert call_args[1].get("analysts") == ["tech", "fund"]
