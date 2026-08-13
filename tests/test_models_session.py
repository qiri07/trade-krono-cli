"""测试模型常驻会话（Phase 4）。"""
import pytest
from unittest.mock import MagicMock, patch

from trade_krono_cli.models.kronos_session import KronosSession
from trade_krono_cli.models.ta_session import TASession
from trade_krono_cli.kronos_runner import KronosForecastResult
from trade_krono_cli.ta_runner import StockAnalysisResult


class TestKronosSession:
    """KronosSession 单元测试。"""

    def test_default_init_creates_runner(self):
        """默认初始化创建 KronosRunner。"""
        with patch("trade_krono_cli.models.kronos_session.KronosRunner") as MockRunner:
            mock_runner = MagicMock()
            MockRunner.return_value = mock_runner
            session = KronosSession()
            MockRunner.assert_called_once()
            assert session.runner is mock_runner

    def test_is_loaded_false_by_default(self):
        """默认状态下模型未加载。"""
        with patch("trade_krono_cli.models.kronos_session.KronosRunner") as MockRunner:
            mock_runner = MagicMock()
            mock_runner._kronos_adapter = None
            MockRunner.return_value = mock_runner
            with patch.object(KronosSession, 'is_loaded', new_callable=lambda: property(lambda self: False)):
                session = KronosSession()
                assert session.is_loaded is False

    def test_ensure_loaded_calls_load(self):
        """ensure_loaded 触发模型加载。"""
        with patch("trade_krono_cli.models.kronos_session.KronosRunner") as MockRunner:
            mock_runner = MagicMock()
            mock_runner._kronos_adapter = None
            MockRunner.return_value = mock_runner
            with patch.object(KronosSession, 'is_loaded', new_callable=lambda: property(lambda self: False)):
                session = KronosSession()
                session.ensure_loaded()
                mock_runner._load.assert_called_once()

    def test_ensure_loaded_noop_when_already_loaded(self):
        """已加载时 ensure_loaded 是 no-op。"""
        with patch("trade_krono_cli.models.kronos_session.KronosRunner") as MockRunner:
            mock_runner = MagicMock()
            mock_runner._kronos_adapter = MagicMock()
            MockRunner.return_value = mock_runner
            with patch.object(KronosSession, 'is_loaded', new_callable=lambda: property(lambda self: True)):
                session = KronosSession()
                session.ensure_loaded()
                session.ensure_loaded()  # 第二次调用
                assert mock_runner._load.call_count == 0

    def test_predict_batch_delegates(self):
        """predict_batch 委托给 runner。"""
        with patch("trade_krono_cli.models.kronos_session.KronosRunner") as MockRunner:
            mock_runner = MagicMock()
            mock_runner._kronos_adapter = MagicMock()
            type(mock_runner)._adapter = property(lambda self: mock_runner._kronos_adapter)
            mock_runner.predict_batch.return_value = []
            MockRunner.return_value = mock_runner
            session = KronosSession()
            session.predict_batch(["sh.600519"], "2026-08-12")
            mock_runner.predict_batch.assert_called_once()


class TestTASession:
    """TASession 单元测试。"""

    def test_default_init_creates_runner(self):
        """默认初始化创建 TradingAgentsRunner。"""
        with patch("trade_krono_cli.models.ta_session.TradingAgentsRunner") as MockRunner:
            mock_runner = MagicMock()
            MockRunner.return_value = mock_runner
            session = TASession()
            MockRunner.assert_called_once()
            assert session.runner is mock_runner

    def test_is_loaded_false_by_default(self):
        """默认状态下 graph 未初始化。"""
        with patch("trade_krono_cli.models.ta_session.TradingAgentsRunner") as MockRunner:
            mock_runner = MagicMock()
            mock_runner._adapter = None
            MockRunner.return_value = mock_runner
            session = TASession()
            assert session.is_loaded is False

    def test_ensure_loaded_calls_get_graph(self):
        """ensure_loaded 触发 graph 初始化。"""
        with patch("trade_krono_cli.models.ta_session.TradingAgentsRunner") as MockRunner:
            mock_runner = MagicMock()
            mock_runner._adapter = None
            MockRunner.return_value = mock_runner
            session = TASession()
            session.ensure_loaded()
            mock_runner._get_adapter.assert_called_once()

    def test_ensure_loaded_noop_when_already_loaded(self):
        """已初始化时 ensure_loaded 是 no-op。"""
        with patch("trade_krono_cli.models.ta_session.TradingAgentsRunner") as MockRunner:
            mock_runner = MagicMock()
            mock_runner._adapter = {"run_analysis": MagicMock()}
            MockRunner.return_value = mock_runner
            session = TASession()
            session.ensure_loaded()
            session.ensure_loaded()  # 第二次调用应跳过
            assert mock_runner._get_adapter.call_count == 0

    def test_analyze_batch_delegates(self):
        """analyze_batch 委托给 runner。"""
        with patch("trade_krono_cli.models.ta_session.TradingAgentsRunner") as MockRunner:
            mock_runner = MagicMock()
            mock_runner._adapter = {}
            mock_runner.analyze_batch.return_value = []
            MockRunner.return_value = mock_runner
            session = TASession()
            session.analyze_batch(["sh.600519"], "2026-08-12")
            mock_runner.analyze_batch.assert_called_once()
