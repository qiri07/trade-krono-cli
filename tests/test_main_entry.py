"""trade_krono_cli.__main__ — 模块入口单元测试。

验证 `python -m trade_krono_cli` 入口能正确导入并调用 cli.main。
"""

from __future__ import annotations

from unittest.mock import patch


def test_main_calls_cli_main() -> None:
    """__main__ 应该委托给 trade_krono_cli.cli.main。"""
    with patch("trade_krono_cli.cli.main") as mock_main:
        from trade_krono_cli import __main__

        __main__.main()
        mock_main.assert_called_once()
