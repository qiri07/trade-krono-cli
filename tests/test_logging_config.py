"""测试结构化日志模块（Phase 3）。"""

import json
from io import StringIO

from loguru import logger

from trade_krono_cli.logging_config import (
    error_structured,
    info_structured,
    setup_logger,
    warning_structured,
)


def _capture_logs(fn):
    """辅助：捕获 logger 输出到 StringIO。"""
    buf = StringIO()
    setup_logger(level="DEBUG", json_format=False, sink=buf)
    fn(buf)
    return buf.getvalue()


class TestSetupLogger:
    def test_setup_text_mode(self) -> None:
        """Text 模式下输出可读日志。"""
        output = _capture_logs(lambda buf: logger.info("test message"))
        assert "test message" in output

    def test_setup_json_mode(self) -> None:
        """Json 模式下输出合法 JSON 行。"""
        setup_logger(level="INFO", json_format=True)
        logger.info("test json message")
        import trade_krono_cli.logging_config as _mod

        sink = getattr(_mod, "_json_sink", None)
        assert sink is not None
        assert len(sink.records) >= 1
        entry = json.loads(sink.records[-1])
        assert entry["level"] == "INFO"
        assert entry["message"] == "test json message"


class TestStructuredHelpers:
    def test_info_structured(self) -> None:
        output = _capture_logs(lambda buf: info_structured("hello", symbol="sh.600519", score=85.0))
        assert "hello" in output

    def test_error_structured(self) -> None:
        output = _capture_logs(lambda buf: error_structured("boom", module="kronos"))
        assert "boom" in output

    def test_warning_structured(self) -> None:
        output = _capture_logs(lambda buf: warning_structured("warn msg", code="T1_LOCKED"))
        assert "warn msg" in output
