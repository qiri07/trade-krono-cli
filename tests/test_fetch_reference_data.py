"""tests for scripts.fetch_reference_data."""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock, patch

import scripts.fetch_reference_data as frd
from scripts.fetch_reference_data import _fetch_metadata_one, _fetch_metadata_one_hard


class TestGetAllTickers:
    """Ticker retrieval from DB."""

    def test_returns_list(self) -> None:
        with patch("scripts._utils.sqlite3.connect") as mock_connect:
            mock_conn = mock_connect.return_value
            mock_conn.execute.return_value.fetchall.return_value = [
                ("sh.600519",),
                ("sz.000001",),
            ]
            from scripts._utils import get_all_tickers

            tickers = get_all_tickers()
            assert isinstance(tickers, list)
            assert len(tickers) > 0

    def test_contains_known_tickers(self) -> None:
        """At minimum sz.000001 should always be present (whitelist stock)."""
        with patch("scripts._utils.sqlite3.connect") as mock_connect:
            mock_conn = mock_connect.return_value
            mock_conn.execute.return_value.fetchall.return_value = [
                ("sz.000001",),
                ("sh.600519",),
            ]
            from scripts._utils import get_all_tickers

            tickers = get_all_tickers()
            assert "sz.000001" in tickers
            assert len(tickers) > 0


class TestFetchMetadataOne:
    """Single stock metadata fetch."""

    def test_success(self) -> None:
        """Successful metadata fetch returns correct structure."""
        mock_factory = MagicMock()
        mock_meta = MagicMock()
        mock_meta.ticker = "sh.600519"
        mock_meta.industry = "银行"
        mock_meta.pe_ttm = 12.5
        mock_meta.pb = 1.2
        mock_meta.ipo_date = "2001-08-27"
        mock_meta.is_st = False
        mock_meta.source = "tonghuashun"
        mock_factory.fetch_metadata.return_value = mock_meta

        ticker, meta, status = _fetch_metadata_one(mock_factory, "sh.600519")
        assert status == "ok"
        assert meta is not None
        assert meta["ticker"] == "sh.600519"
        assert meta["industry"] == "银行"
        assert meta["ipo_date"] == "2001-08-27"

    def test_none_result(self) -> None:
        """When fetch_metadata returns None, status is 'none'."""
        mock_factory = MagicMock()
        mock_factory.fetch_metadata.return_value = None

        ticker, meta, status = _fetch_metadata_one(mock_factory, "sh.999999")
        assert status == "none"
        assert meta is None

    def test_exception(self) -> None:
        """Exceptions are caught and returned as error status."""
        mock_factory = MagicMock()
        mock_factory.fetch_metadata.side_effect = RuntimeError("API timeout")

        ticker, meta, status = _fetch_metadata_one(mock_factory, "sh.600519")
        assert status.startswith("error:")
        assert meta is None


class TestFetchMetadataOneHard:
    """_fetch_metadata_one_hard — 带硬超时的单只拉取（修复 baostock 会话挂起导致的整批卡死）。

    核心保障：即使底层 fetch 永久 hang，worker 线程也在 FETCH_PER_TICKER_TIMEOUT
    内有界返回，从而 ThreadPoolExecutor.shutdown(wait=True) 不再被拖死。
    """

    def test_normal_success_passes_through(self) -> None:
        """正常路径：底层快速完成，硬超时版本直接透传结果。"""
        mock_factory = MagicMock()
        mock_meta = MagicMock()
        mock_meta.ticker = "sh.600519"
        mock_meta.industry = "银行"
        mock_meta.pe_ttm = 12.5
        mock_meta.pb = 1.2
        mock_meta.ipo_date = "2001-08-27"
        mock_meta.is_st = False
        mock_meta.source = "tonghuashun"
        mock_factory.fetch_metadata.return_value = mock_meta

        ticker, meta, status = _fetch_metadata_one_hard(mock_factory, "sh.600519")
        assert status == "ok"
        assert meta is not None
        assert meta["industry"] == "银行"

    def test_hanging_fetch_returns_timeout_not_infinite(self, monkeypatch) -> None:
        """底层 fetch 永久 hang 时，worker 在硬超时后返回 'timeout' 而非卡死。"""
        import threading as _threading

        barrier = _threading.Event()

        def _hang_forever(_ticker):
            barrier.set()  # 通知：已真正阻塞
            while True:
                _threading.Event().wait(1)

        mock_factory = MagicMock()
        mock_factory.fetch_metadata.side_effect = _hang_forever

        # 把硬超时压到 1s，避免单测等待 180s
        monkeypatch.setattr(frd, "FETCH_PER_TICKER_TIMEOUT", 1)
        start = time.time()
        ticker, meta, status = frd._fetch_metadata_one_hard(mock_factory, "sh.600519")
        elapsed = time.time() - start

        assert status == "timeout"
        assert meta is None
        assert ticker == "sh.600519"
        # 关键断言：有界返回（应在 ~1s + 少量开销内完成，而非永久挂起）
        assert elapsed < 15, f"硬超时未生效，耗时 {elapsed:.1f}s 远超 1s"
        assert barrier.is_set(), "未真正触达底层 fetch（mock 失效）"

    def test_bounded_shutdown_not_blocked(self, monkeypatch) -> None:
        """回归：模拟批次循环，确认含 hang 票的批次 shutdown(wait=True) 有界完成。

        复现 2026-10-31 批次 51 卡 27 分钟的场景——20 个 worker 中 1 个永久 hang，
        硬超时后整批应在有限时间内推进到下一批，而非被 shutdown 拖死。
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        hang_called = {"n": 0}

        # mock_factory.fetch_metadata 的 side_effect 收到的实参是 ticker（单个），
        # sh.HANG 走永久阻塞模拟 baostock 会话挂起，其余正常票正常返回
        mock_factory = MagicMock()
        ok_meta = MagicMock(
            ticker="sh.600519", industry="银行", pe_ttm=1.0, pb=1.0,
            ipo_date="2001-08-27", is_st=False, source="tonghuashun",
        )

        def _metadata_side_effect(_ticker):
            if _ticker == "sh.HANG":
                hang_called["n"] += 1
                while True:
                    time.sleep(1)
            return ok_meta

        mock_factory.fetch_metadata.side_effect = _metadata_side_effect

        monkeypatch.setattr(frd, "FETCH_PER_TICKER_TIMEOUT", 2)

        tickers = ["sh.600519", "sh.000858", "sh.HANG", "sz.000001"]
        start = time.time()
        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {pool.submit(frd._fetch_metadata_one_hard, mock_factory, t): t for t in tickers}
            for fut in as_completed(futures):
                key, meta, status = fut.result(timeout=30)
                assert key is not None
        elapsed = time.time() - start
        # 关键：含 1 只永久 hang 票的批次，整批应在有限时间（远小于旧实现的 27min）完成
        assert hang_called["n"] >= 1
        assert elapsed < 60, f"批次被 hang 票拖死：耗时 {elapsed:.1f}s"


class TestCheckpoint:
    """load_checkpoint / save_checkpoint / clear_checkpoint 原子性与恢复逻辑。"""

    def test_save_then_load_roundtrip(self, monkeypatch, tmp_path) -> None:
        """save_checkpoint 写入后 load_checkpoint 能读回相同数据。"""
        cp = tmp_path / "checkpoint.json"
        monkeypatch.setattr(frd, "CHECKPOINT_FILE", cp)
        data = {"sh.600519": {"pe_ttm": 21.0}, "sz.000001": None}
        frd.save_checkpoint(data)
        assert frd.load_checkpoint() == data

    def test_load_missing_returns_empty(self, monkeypatch, tmp_path) -> None:
        """checkpoint 文件不存在时 load_checkpoint 返回空 dict。"""
        monkeypatch.setattr(frd, "CHECKPOINT_FILE", tmp_path / "nope.json")
        assert frd.load_checkpoint() == {}

    def test_clear_removes_file(self, monkeypatch, tmp_path) -> None:
        """clear_checkpoint 删除文件；缺失时不报错。"""
        cp = tmp_path / "checkpoint.json"
        monkeypatch.setattr(frd, "CHECKPOINT_FILE", cp)
        cp.write_text("{}", encoding="utf-8")
        frd.clear_checkpoint()
        assert not cp.exists()
        frd.clear_checkpoint()  # 幂等

    def test_corrupt_checkpoint_returns_empty(self, monkeypatch, tmp_path) -> None:
        """损坏的 checkpoint 文件 load_checkpoint 降级返回空 dict，不 raise。"""
        cp = tmp_path / "checkpoint.json"
        monkeypatch.setattr(frd, "CHECKPOINT_FILE", cp)
        cp.write_text("{not valid", encoding="utf-8")
        assert frd.load_checkpoint() == {}

    def test_resume_skips_success_only_refetches_failures(
        self, monkeypatch, tmp_path
    ) -> None:
        """resume=True 时仅重拉 checkpoint 中为 None/缺失的 ticker。"""
        cp = tmp_path / "checkpoint.json"
        monkeypatch.setattr(frd, "CHECKPOINT_FILE", cp)
        monkeypatch.setattr(
            frd, "get_all_tickers", lambda: ["sh.600519", "sh.000858", "sh.601318"]
        )
        # 预置 checkpoint：茅台成功、五粮液失败（None）、平安未拉（缺失）
        cp.write_text(
            json.dumps(
                {
                    "sh.600519": {"ticker": "sh.600519", "pe_ttm": 21.0},
                    "sh.000858": None,
                }
            ),
            encoding="utf-8",
        )

        # mock factory + 拉取：仅被拉到的 ticker 记录
        fetched: list[str] = []

        class _Factory:
            def fetch_metadata(self, t: str):
                fetched.append(t)
                m = MagicMock()
                m.ticker = t
                m.industry = "x"
                m.pe_ttm = 1.0
                m.pb = 1.0
                m.ipo_date = "2001-01-01"
                m.is_st = False
                m.source = "test"
                return m

        monkeypatch.setattr(frd, "get_data_factory", lambda: _Factory())
        monkeypatch.setattr(frd, "METADATA_FILE", tmp_path / "metadata.json")

        frd.main(tickers_input=None, resume=True, use_checkpoint=True)

        # 断点续传应跳过已成功的 sh.600519，只重拉 sh.000858 + sh.601318
        assert "sh.600519" not in fetched, "已成功 ticker 被重复拉取"
        assert set(fetched) == {"sh.000858", "sh.601318"}
        # 成功后 checkpoint 被清除
        assert not cp.exists()
