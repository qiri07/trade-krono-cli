"""logger、globals、cli_commands 和 utils 的测试。"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

from trade_krono_cli.globals import clear_all_globals
from trade_krono_cli.logger import setup_logger
from trade_krono_cli.utils import add_ticker_prefix, safe_float, strip_ticker_prefix

if TYPE_CHECKING:
    from pathlib import Path


class TestLogger:
    """logger 模块测试。"""

    def test_setup_logger_basic(self, tmp_path: Path) -> None:
        log_file = tmp_path / "test.log"
        with patch("trade_krono_cli.logger.get_settings") as mock_settings:
            mock_settings.return_value.cache_dir = tmp_path
            setup_logger(level="DEBUG", log_file=log_file)
        assert log_file.exists()


class TestGlobals:
    """globals 模块测试。"""

    def test_clear_all_globals(self) -> None:
        clear_all_globals()

    def test_clear_multiple_times(self) -> None:
        clear_all_globals()
        clear_all_globals()


class TestUtils:
    """utils 工具函数测试。"""

    def test_safe_float_valid(self) -> None:
        assert safe_float(3.14) == 3.14
        assert safe_float("3.14") == 3.14
        assert safe_float(0) == 0.0
        assert safe_float(None) is None

    def test_safe_float_invalid(self) -> None:
        assert safe_float("abc") is None
        assert safe_float("") is None

    def test_safe_float_nan_inf(self) -> None:
        assert safe_float(float("nan")) is None
        assert safe_float(float("inf")) is None
        assert safe_float(float("-inf")) is None

    def test_strip_ticker_prefix_sh(self) -> None:
        assert strip_ticker_prefix("sh.600519") == "600519"

    def test_strip_ticker_prefix_sz(self) -> None:
        assert strip_ticker_prefix("sz.000858") == "000858"

    def test_strip_ticker_prefix_bj(self) -> None:
        assert strip_ticker_prefix("bj.920000") == "920000"

    def test_strip_ticker_prefix_already_plain(self) -> None:
        assert strip_ticker_prefix("600519") == "600519"

    def test_add_ticker_prefix_sh(self) -> None:
        assert add_ticker_prefix("600519") == "sh.600519"
        assert add_ticker_prefix("500000") == "sh.500000"

    def test_add_ticker_prefix_sz(self) -> None:
        assert add_ticker_prefix("000858") == "sz.000858"
        assert add_ticker_prefix("300750") == "sz.300750"

    def test_add_ticker_prefix_bj(self) -> None:
        assert add_ticker_prefix("920000") == "bj.920000"

    def test_add_ticker_prefix_invalid(self) -> None:
        assert add_ticker_prefix("abc") == "abc"
        assert add_ticker_prefix("600519x") == "600519x"

    def test_add_ticker_prefix_already_prefixed(self) -> None:
        assert add_ticker_prefix("sh.600519") == "sh.600519"


class TestCliCommands:
    """cli_commands 模块测试。"""

    def test_maintenance_status(self) -> None:
        from trade_krono_cli.cli_commands.maintenance_status import status

        # status() 返回 None，只验证不抛异常
        status()
