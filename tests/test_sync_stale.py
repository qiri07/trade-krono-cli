#!/usr/bin/env python3
"""scripts.sync_stale 单元测试。"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from sync_stale import _WORKERS, main  # noqa: E402


class TestSyncStale:
    """测试 sync_stale 模块。"""

    def test_workers_constant(self) -> None:
        """_WORKERS 常量正确。"""
        assert _WORKERS == 16

    @patch("scripts.sync_stale._load_env")
    @patch("scripts.sync_stale.get_expected_date", return_value="2026-09-07")
    @patch("sync_stale.fetch_kline_incremental")
    def test_sync_single_ticker_success(
        self, mock_fetch: MagicMock, mock_get_date: MagicMock, mock_load_env: MagicMock
    ) -> None:
        """成功同步单个 ticker。"""
        mock_df = MagicMock()
        mock_df.__len__ = MagicMock(return_value=100)
        mock_fetch.return_value = mock_df

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("sh.600519\n")
            ticker_file = f.name

        try:
            with patch("sys.argv", ["sync_stale.py", ticker_file]):
                result = main()
                # 成功时 fail=0，返回 0
                assert result == 0
                mock_fetch.assert_called_once()
        finally:
            Path(ticker_file).unlink(missing_ok=True)

    @patch("scripts.sync_stale._load_env")
    @patch("scripts.sync_stale.get_expected_date", return_value="2026-09-07")
    @patch("sync_stale.fetch_kline_incremental")
    def test_sync_ticker_failure(
        self, mock_fetch: MagicMock, mock_get_date: MagicMock, mock_load_env: MagicMock
    ) -> None:
        """同步失败时正确处理。"""
        mock_fetch.return_value = None

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("sh.600519\n")
            ticker_file = f.name

        try:
            with patch("sys.argv", ["sync_stale.py", ticker_file]):
                result = main()
                # 失败率 100% > 10%，返回 1
                assert result == 1
        finally:
            Path(ticker_file).unlink(missing_ok=True)

    @patch("scripts.sync_stale._load_env")
    @patch("scripts.sync_stale.get_expected_date", return_value="2026-09-07")
    @patch("sync_stale.fetch_kline_incremental")
    def test_sync_mixed_result(
        self, mock_fetch: MagicMock, mock_get_date: MagicMock, mock_load_env: MagicMock
    ) -> None:
        """混合成功/失败场景。"""

        # 前 9 个成功，1 个失败（失败率 10%，刚好不超限）
        def side_effect(ticker, *args, **kwargs):
            if ticker == "sh.999999":
                return None
            df = MagicMock()
            df.__len__ = MagicMock(return_value=100)
            return df

        mock_fetch.side_effect = side_effect

        tickers = [f"sh.{i:06d}" for i in range(1000, 1010)]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("\n".join(tickers) + "\n")
            ticker_file = f.name

        try:
            with patch("sys.argv", ["sync_stale.py", ticker_file]):
                result = main()
                # 失败率 10% = 1/10，不超过 10%，返回 0
                assert result == 0
        finally:
            Path(ticker_file).unlink(missing_ok=True)

    @patch("scripts.sync_stale._load_env")
    @patch("scripts.sync_stale.get_expected_date", return_value="2026-09-07")
    @patch("sync_stale.fetch_kline_incremental")
    def test_sync_high_failure_rate(
        self, mock_fetch: MagicMock, mock_get_date: MagicMock, mock_load_env: MagicMock
    ) -> None:
        """高失败率场景。"""
        mock_fetch.return_value = None

        tickers = [f"sh.{i:06d}" for i in range(1000, 1010)]
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("\n".join(tickers) + "\n")
            ticker_file = f.name

        try:
            with patch("sys.argv", ["sync_stale.py", ticker_file]):
                result = main()
                # 失败率 100% > 10%，返回 1
                assert result == 1
        finally:
            Path(ticker_file).unlink(missing_ok=True)

    def test_empty_ticker_file(self, tmp_path: Path) -> None:
        """空文件处理。"""
        ticker_file = tmp_path / "empty.txt"
        ticker_file.write_text("")

        with (
            patch("sys.argv", ["sync_stale.py", str(ticker_file)]),
            patch("scripts.sync_stale._load_env"),
            patch("scripts.sync_stale.get_expected_date"),
            patch("scripts.sync_stale.fetch_kline_incremental"),
        ):
            result = main()
            # 空文件，success=0, fail=0，0 < 0*0.1 为 False，返回 1
            assert result == 1

    def test_whitespace_lines_skipped(self, tmp_path: Path) -> None:
        """空白行被跳过。"""
        ticker_file = tmp_path / "whitespace.txt"
        ticker_file.write_text("\n\nsh.600519\n\nsh.600000\n\n")

        with (
            patch("sys.argv", ["sync_stale.py", str(ticker_file)]),
            patch("scripts.sync_stale._load_env"),
            patch("scripts.sync_stale.get_expected_date"),
            patch("sync_stale.fetch_kline_incremental") as mock_fetch,
        ):
            mock_df = MagicMock()
            mock_df.__len__ = MagicMock(return_value=100)
            mock_fetch.return_value = mock_df

            result = main()
            assert mock_fetch.call_count == 2  # 只处理非空行
            assert result == 0

    @patch("scripts.sync_stale._load_env")
    @patch("scripts.sync_stale.get_expected_date", return_value="2026-09-07")
    @patch("sync_stale.fetch_kline_incremental")
    def test_exception_handling(
        self, mock_fetch: MagicMock, mock_get_date: MagicMock, mock_load_env: MagicMock
    ) -> None:
        """异常时被捕获并计数为失败。"""
        mock_fetch.side_effect = Exception("API error")

        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("sh.600519\n")
            ticker_file = f.name

        try:
            with patch("sys.argv", ["sync_stale.py", ticker_file]):
                result = main()
                assert result == 1
        finally:
            Path(ticker_file).unlink(missing_ok=True)
