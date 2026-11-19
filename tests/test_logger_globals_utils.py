"""logger、globals、cli_commands 和 utils 的测试。"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import patch

from trade_krono_cli.globals import clear_all_globals
from trade_krono_cli.logger import (
    _RotatingJsonFileSink,
    setup_logger,
)
from trade_krono_cli.logger import (
    logger as lg,
)
from trade_krono_cli.utils import add_ticker_prefix, safe_float, strip_ticker_prefix

if TYPE_CHECKING:
    from pathlib import Path


def _fake_record(message: str = "hello") -> SimpleNamespace:
    """构造 loguru Message 兼容对象（仅含 sink 用到的字段）。"""
    return SimpleNamespace(
        record={
            "time": datetime.now(timezone.utc),
            "level": SimpleNamespace(name="INFO"),
            "name": "test",
            "function": "test_fn",
            "line": 1,
            "message": message,
            "extra": {},
        }
    )


class TestLogger:
    """logger 模块测试。"""

    def test_setup_logger_basic(self, tmp_path: Path) -> None:
        log_file = tmp_path / "test.log"
        with patch("trade_krono_cli.logger.get_settings") as mock_settings:
            mock_settings.return_value.cache_dir = tmp_path
            setup_logger(level="DEBUG", log_file=log_file)
        assert log_file.exists()

    def test_production_mode_writes_valid_json(self, tmp_path: Path) -> None:
        """生产模式：pipeline.json 写出合法单行 JSON（不再抛 KeyError: '"ts"'）。"""
        cache_dir = tmp_path / "cache"
        cache_dir.mkdir()
        try:
            with patch("trade_krono_cli.logger.get_settings") as mock_settings:
                mock_settings.return_value.cache_dir = cache_dir
                setup_logger(level="INFO", log_file=tmp_path / "pipeline.log")
            lg.info("含 {花括号} 的消息")
            lg.complete()
            lines = (tmp_path / "pipeline.json").read_text(encoding="utf-8").strip().splitlines()
            assert len(lines) == 1
            assert json.loads(lines[0])["message"] == "含 {花括号} 的消息"
        finally:
            lg.remove()


class TestRotatingJsonFileSink:
    """_RotatingJsonFileSink：单行 JSON 写入 + 按大小轮转。"""

    def test_writes_single_json_line(self, tmp_path: Path) -> None:
        path = tmp_path / "pipeline.json"
        _RotatingJsonFileSink(path)(_fake_record("含 {花括号} 的中文"))
        line = path.read_text(encoding="utf-8").strip()
        assert json.loads(line)["message"] == "含 {花括号} 的中文"

    def test_rotates_when_exceeding_max_bytes(self, tmp_path: Path) -> None:
        path = tmp_path / "pipeline.json"
        sink = _RotatingJsonFileSink(path, max_bytes=50, retention=3)
        for _ in range(5):
            sink(_fake_record("x" * 40))
        assert path.exists()
        assert (tmp_path / "pipeline.json.1").exists()
        assert (tmp_path / "pipeline.json.2").exists()
        assert (tmp_path / "pipeline.json.3").exists()
        assert not (tmp_path / "pipeline.json.4").exists()

    def test_deletes_rotated_files_older_than_max_age(self, tmp_path: Path) -> None:
        """超过 max_age_days 的已轮转文件被删除，当前文件永不删。"""
        import time

        path = tmp_path / "pipeline.json"
        # retention 给大一点，避免按数量删除干扰「按时间删除」的验证
        sink = _RotatingJsonFileSink(path, max_bytes=50, retention=10, max_age_days=1)
        # 预先造一个 2 天前的 .1 轮转文件（mtime 早于 1 天阈值）
        aged = tmp_path / "pipeline.json.1"
        aged.write_text("aged\n", encoding="utf-8")
        old = time.time() - 2 * 86400
        os.utime(aged, (old, old))
        # 写一条日志触发 _delete_aged
        sink(_fake_record("hello"))
        assert not aged.exists()
        assert path.exists()


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
