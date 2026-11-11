"""统一股票池过滤层测试（scripts._utils.filter_stock_universe 等）。

覆盖：
  - 北交所 / 港股 / ETF 前缀过滤
  - ST 判定（is_st 标记与名称关键词两条路径）
  - PE / PB / 市值阈值过滤
  - exclusion_reason 单只票原因分类
  - load_metadata_index 键归一化（带前缀 + 裸代码双索引）
  - intraday_scan 指标包装与 scripts.indicators 数值一致性
"""

from __future__ import annotations

import json
import math
from typing import TYPE_CHECKING
from unittest.mock import patch

import pandas as pd
import pytest

if TYPE_CHECKING:
    from pathlib import Path

from scripts._utils import (
    PE_MAX_TECH,
    exclusion_reason,
    filter_stock_universe,
    is_st_name,
    load_metadata_index,
)


def _meta(**kwargs: object) -> dict[str, object]:
    return dict(kwargs)


class TestPrefixFilters:
    """交易所前缀 / 代码段过滤。"""

    def test_excludes_bj_and_hk_by_default(self) -> None:
        tickers = ["sh.600519", "sz.000001", "bj.920001", "hk.00700"]
        assert filter_stock_universe(tickers) == ["sh.600519", "sz.000001"]

    def test_bare_bse_code_excluded(self) -> None:
        assert filter_stock_universe(["920001", "600519"]) == ["600519"]

    def test_keep_hk_when_disabled(self) -> None:
        assert filter_stock_universe(["hk.00700", "sh.600519"], exclude_hk=False) == [
            "hk.00700",
            "sh.600519",
        ]

    def test_keep_bse_when_disabled(self) -> None:
        assert filter_stock_universe(["bj.920001"], exclude_bse=False) == ["bj.920001"]

    def test_exclude_etf_when_requested(self) -> None:
        tickers = ["sh.600519", "sh.510300", "sz.159915"]
        assert filter_stock_universe(tickers) == tickers
        assert filter_stock_universe(tickers, exclude_etf=True) == ["sh.600519"]

    def test_etf_kept_by_default(self) -> None:
        assert filter_stock_universe(["sh.510300"]) == ["sh.510300"]


class TestStFilters:
    """ST 判定：is_st 标记优先，其次名称关键词。"""

    def test_is_st_flag_excluded(self) -> None:
        meta = {"sh.600001": _meta(name="某某股份", is_st=True)}
        assert filter_stock_universe(["sh.600001"], meta) == []

    def test_st_name_keyword_excluded(self) -> None:
        meta = {"sh.600001": _meta(name="*ST 某某")}
        assert filter_stock_universe(["sh.600001"], meta) == []

    def test_delisted_keyword_excluded(self) -> None:
        meta = {"sh.600001": _meta(name="某某退市")}
        assert filter_stock_universe(["sh.600001"], meta) == []

    def test_normal_name_kept(self) -> None:
        meta = {"sh.600519": _meta(name="贵州茅台")}
        assert filter_stock_universe(["sh.600519"], meta) == ["sh.600519"]

    def test_st_check_skipped_without_metadata(self) -> None:
        # 无元数据时不做名称判断，仅做前缀过滤
        assert filter_stock_universe(["sh.600001"]) == ["sh.600001"]

    def test_st_filter_can_be_disabled(self) -> None:
        meta = {"sh.600001": _meta(name="*ST 某某")}
        assert filter_stock_universe(["sh.600001"], meta, exclude_st=False) == ["sh.600001"]

    def test_bare_code_key_lookup(self) -> None:
        meta = {"600001": _meta(name="*ST 某某")}
        assert filter_stock_universe(["sh.600001"], meta) == []


class TestValuationFilters:
    """PE / PB / 市值阈值。"""

    def test_pe_above_max_excluded(self) -> None:
        meta = {"sh.600519": _meta(pe_ttm=PE_MAX_TECH + 0.1)}
        assert filter_stock_universe(["sh.600519"], meta, max_pe=PE_MAX_TECH) == []

    def test_pe_below_max_kept(self) -> None:
        meta = {"sh.600519": _meta(pe_ttm=PE_MAX_TECH - 0.1)}
        assert filter_stock_universe(["sh.600519"], meta, max_pe=PE_MAX_TECH) == ["sh.600519"]

    def test_non_positive_pe_excluded(self) -> None:
        meta = {"sh.600519": _meta(pe_ttm=0), "sz.000001": _meta(pe_ttm=-3.2)}
        assert filter_stock_universe(["sh.600519", "sz.000001"], meta, max_pe=30) == []

    def test_missing_pe_kept(self) -> None:
        # 元数据缺 PE（如 ETF / 数据缺失）时不拦，交由后续环节判断
        meta = {"sh.600519": _meta(name="贵州茅台")}
        assert filter_stock_universe(["sh.600519"], meta, max_pe=30) == ["sh.600519"]

    def test_pb_below_min_excluded(self) -> None:
        meta = {"sh.600519": _meta(pb=0.5)}
        assert filter_stock_universe(["sh.600519"], meta, min_pb=1.0) == []

    def test_market_cap_below_min_excluded(self) -> None:
        meta = {"sh.600519": _meta(market_cap=12.0)}
        assert filter_stock_universe(["sh.600519"], meta, min_market_cap=50.0) == []

    def test_combined_filters(self) -> None:
        meta = {
            "sh.600519": _meta(name="贵州茅台", pe_ttm=24.0, pb=8.0, market_cap=20000.0),
            "sz.000001": _meta(name="平安银行", pe_ttm=5.0, pb=0.6, market_cap=3000.0),
            "sh.600002": _meta(name="小盘股", pe_ttm=12.0, pb=2.0, market_cap=20.0),
        }
        kept = filter_stock_universe(list(meta), meta, max_pe=30, min_pb=1.0, min_market_cap=50.0)
        assert kept == ["sh.600519"]


class TestExclusionReason:
    """单只票剔除原因分类。"""

    def test_reason_priority(self) -> None:
        assert exclusion_reason("hk.00700") == "hk"
        assert exclusion_reason("bj.920001") == "bse"
        assert exclusion_reason("920001") == "bse"
        assert exclusion_reason("sh.510300", exclude_etf=True) == "etf"
        assert exclusion_reason("sh.600001", {"sh.600001": _meta(is_st=True)}) == "st"
        assert exclusion_reason("sh.519999") is None

    def test_hk_checked_before_etf(self) -> None:
        assert exclusion_reason("hk.00700", exclude_etf=True) == "hk"


class TestLoadMetadataIndex:
    """metadata.json 键归一化。"""

    def _write(self, tmp_path: Path, payload: dict[str, object]) -> None:
        (tmp_path / "metadata.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )

    def test_bare_and_prefixed_keys(self, tmp_path: Path) -> None:
        self._write(tmp_path, {"600519": {"name": "贵州茅台", "pe_ttm": 24.0}})
        index = load_metadata_index(tmp_path)
        assert index["sh.600519"]["name"] == "贵州茅台"
        assert index["600519"]["name"] == "贵州茅台"

    def test_prefixed_input_normalized(self, tmp_path: Path) -> None:
        self._write(tmp_path, {"sz.000001": {"name": "平安银行"}})
        index = load_metadata_index(tmp_path)
        assert "sz.000001" in index
        assert "000001" in index

    def test_include_bare_keys_disabled(self, tmp_path: Path) -> None:
        self._write(tmp_path, {"600519": {"name": "贵州茅台"}})
        index = load_metadata_index(tmp_path, include_bare_keys=False)
        assert list(index) == ["sh.600519"]

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        assert load_metadata_index(tmp_path) == {}

    def test_corrupt_file_returns_empty(self, tmp_path: Path) -> None:
        (tmp_path / "metadata.json").write_text("{not json", encoding="utf-8")
        assert load_metadata_index(tmp_path) == {}

    def test_non_dict_entries_skipped(self, tmp_path: Path) -> None:
        self._write(tmp_path, {"600519": {"name": "贵州茅台"}, "600520": "broken"})
        index = load_metadata_index(tmp_path)
        assert "sh.600519" in index
        assert "sh.600520" not in index


class TestIsStName:
    """ST 名称关键词判断。"""

    def test_full_keywords_detect_new_stock(self) -> None:
        assert is_st_name("N 新股") is True
        assert is_st_name("C 次新") is True

    def test_minimal_keywords_ignore_new_stock(self) -> None:
        from scripts._utils import ST_NAME_KEYWORDS_MINIMAL

        assert is_st_name("N 新股", ST_NAME_KEYWORDS_MINIMAL) is False

    def test_detects_real_st_names(self) -> None:
        assert is_st_name("*ST金钰") is True
        assert is_st_name("ST中珠") is True
        assert is_st_name("退市海润") is True

    def test_latin_letters_inside_name_not_treated_as_st(self) -> None:
        """含字母 N/C 的正常名称（TCL、MSCI 等）不得误判为 ST。"""
        assert is_st_name("TCL科技") is False
        assert is_st_name("TCL中环") is False
        assert is_st_name("易方达MSCI中国A股国际通ETF") is False
        assert is_st_name("国泰CES芯片ETF") is False
        assert is_st_name("纳斯达克100ETF") is False


class TestIntradayIndicatorDelegation:
    """intraday_scan 的指标包装与 scripts.indicators 数值一致。"""

    def test_calc_ma_matches_pandas(self) -> None:
        from scripts.intraday_scan import _calc_ma

        closes = [10.0, 10.5, 11.0, 10.8, 11.2, 11.5, 11.1, 11.8]
        expected = pd.Series(closes).rolling(5).mean().tolist()
        got = _calc_ma(closes, 5)
        assert len(got) == len(expected)
        for a, b in zip(got, expected, strict=True):
            if math.isnan(a):
                assert math.isnan(b)
            else:
                assert a == pytest.approx(b, rel=1e-12)

    def test_calc_ma_short_series_all_nan(self) -> None:
        from scripts.intraday_scan import _calc_ma

        got = _calc_ma([1.0, 2.0], 5)
        assert len(got) == 2
        assert all(math.isnan(v) for v in got)

    def test_calc_macd_dif_short_series_all_nan(self) -> None:
        from scripts.intraday_scan import _calc_macd_dif

        closes = [float(i) for i in range(10)]
        got = _calc_macd_dif(closes)
        assert len(got) == 10
        assert all(math.isnan(v) for v in got)

    def test_calc_macd_dif_matches_ema(self) -> None:
        from scripts.intraday_scan import _calc_macd_dif

        closes = [float(10 + i % 5) for i in range(40)]
        series = pd.Series(closes)
        expected = (
            series.ewm(span=12, adjust=False).mean() - series.ewm(span=26, adjust=False).mean()
        ).tolist()
        got = _calc_macd_dif(closes)
        for a, b in zip(got, expected, strict=True):
            assert a == pytest.approx(b, rel=1e-12)


class TestIntradayNormalizeTickers:
    """intraday_scan._normalize_tickers 复用统一过滤层。"""

    def test_filters_bse_and_dedupes(self) -> None:
        from scripts.intraday_scan import _normalize_tickers

        with patch("scripts.intraday_scan.logger"):
            got = _normalize_tickers(["600519", "sh.600519", "920001", "000858"])
        assert got == ["sh.600519", "sz.000858"]


class TestPePercentileCache:
    """PE 分位文件缓存（multi_scan._load/_save_pe_percentile_cache）。"""

    def _patch_path(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        import scripts.multi_scan as ms

        cache_file = tmp_path / "pe_percentile_cache.json"
        monkeypatch.setattr(ms, "_pe_percentile_cache_path", lambda: cache_file)

    def test_save_and_load_roundtrip(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        import scripts.multi_scan as ms

        self._patch_path(monkeypatch, tmp_path)
        ms._save_pe_percentile_cache({"600519": 24.5, "000001": 12.0})
        loaded = ms._load_pe_percentile_cache(ttl_days=7)
        assert loaded == {"600519": 24.5, "000001": 12.0}

    def test_missing_file_returns_empty(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import scripts.multi_scan as ms

        self._patch_path(monkeypatch, tmp_path)
        assert ms._load_pe_percentile_cache(ttl_days=7) == {}

    def test_expired_cache_returns_empty(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        from datetime import datetime, timedelta

        import scripts.multi_scan as ms

        self._patch_path(monkeypatch, tmp_path)
        (tmp_path / "pe_percentile_cache.json").write_text(
            json.dumps(
                {
                    "date": (datetime.now().date() - timedelta(days=8)).isoformat(),
                    "data": {"600519": 24.5},
                }
            ),
            encoding="utf-8",
        )
        assert ms._load_pe_percentile_cache(ttl_days=7) == {}

    def test_fetch_pe_percentiles_uses_cache(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        import scripts.multi_scan as ms

        self._patch_path(monkeypatch, tmp_path)
        ms._save_pe_percentile_cache({"600519": 24.5})
        calls: list[str] = []

        def _fake_stock_value_em(symbol: str):
            calls.append(symbol)
            return None

        fake_ak = type("_A", (), {"stock_value_em": staticmethod(_fake_stock_value_em)})()
        monkeypatch.setattr(ms, "_AK", fake_ak)
        result = ms._fetch_pe_percentiles(["600519", "000001"], use_cache=True)
        # 600519 来自缓存，000001 缺失会尝试拉取（返回 None → 不入结果）
        assert result == {"600519": 24.5}
        assert calls == ["000001"]


class TestFilterByPe:
    """multi_scan._filter_by_pe：统一口径 PE∈(0,30)。"""

    def test_pe_in_range_kept(self) -> None:
        from scripts.multi_scan import _filter_by_pe

        results = [
            {"ticker": "sh.600519"},
            {"ticker": "sz.000001"},
        ]
        pe_map = {
            "600519": {"pe": 24.5, "name": "贵州茅台"},
            "000001": {"pe": 6.8, "name": "平安银行"},
        }
        filtered, removed = _filter_by_pe(results, pe_map)
        assert removed == 0
        assert len(filtered) == 2

    def test_pe_below_zero_removed(self) -> None:
        from scripts.multi_scan import _filter_by_pe

        results = [{"ticker": "sh.600001"}]
        pe_map = {"600001": {"pe": -3.2, "name": "亏损股"}}
        filtered, removed = _filter_by_pe(results, pe_map)
        assert removed == 1
        assert filtered == []

    def test_pe_zero_removed(self) -> None:
        from scripts.multi_scan import _filter_by_pe

        results = [{"ticker": "sh.600001"}]
        pe_map = {"600001": {"pe": 0, "name": "零PE"}}
        filtered, removed = _filter_by_pe(results, pe_map)
        assert removed == 1
        assert filtered == []

    def test_pe_at_max_removed(self) -> None:
        from scripts.multi_scan import _filter_by_pe

        results = [{"ticker": "sh.600519"}]
        pe_map = {"600519": {"pe": PE_MAX_TECH, "name": "边界股"}}
        filtered, removed = _filter_by_pe(results, pe_map)
        assert removed == 1
        assert filtered == []

    def test_pe_above_max_removed(self) -> None:
        from scripts.multi_scan import _filter_by_pe

        results = [{"ticker": "sh.600519"}]
        pe_map = {"600519": {"pe": PE_MAX_TECH + 0.1, "name": "高PE股"}}
        filtered, removed = _filter_by_pe(results, pe_map)
        assert removed == 1
        assert filtered == []

    def test_pe_missing_removed(self) -> None:
        from scripts.multi_scan import _filter_by_pe

        results = [{"ticker": "sh.600519"}]
        filtered, removed = _filter_by_pe(results, {})
        assert removed == 1
        assert filtered == []

    def test_name_filled_when_empty(self) -> None:
        from scripts.multi_scan import _filter_by_pe

        results = [{"ticker": "sh.600519"}]
        pe_map = {"600519": {"pe": 24.5, "name": "贵州茅台"}}
        filtered, _ = _filter_by_pe(results, pe_map)
        assert filtered[0]["name"] == "贵州茅台"

    def test_name_not_overwritten(self) -> None:
        from scripts.multi_scan import _filter_by_pe

        results = [{"ticker": "sh.600519", "name": "自定义名称"}]
        pe_map = {"600519": {"pe": 24.5, "name": "贵州茅台"}}
        filtered, _ = _filter_by_pe(results, pe_map)
        assert filtered[0]["name"] == "自定义名称"


class TestEtfTickDetection:
    """ETF 代码段识别（含 52/53/55/56 沪市新段）。"""

    def test_sh_etf_segments(self) -> None:
        from scripts._utils import is_etf_ticker

        for ticker in (
            "sh.510300",
            "sh.520700",
            "sh.530880",
            "sh.551000",
            "sh.562730",
            "sh.588000",
        ):
            assert is_etf_ticker(ticker) is True, ticker

    def test_sh_etf_segments_50x(self) -> None:
        """沪市 ETF 50x/51x/52x/53x/55x/56x/57x/58x 段均识别为沪市 ETF（sh.）。"""
        from scripts._utils import is_etf_ticker

        for ticker in (
            "sh.501030",
            "sh.510310",
            "sh.520700",
            "sh.530880",
            "sh.551000",
            "sh.562730",
        ):
            assert is_etf_ticker(ticker) is True, ticker

    def test_sz_etf_segments(self) -> None:
        from scripts._utils import is_etf_ticker

        for ticker in ("sz.159915", "sz.156100", "sz.158016"):
            assert is_etf_ticker(ticker) is True, ticker

    def test_stock_not_etf(self) -> None:
        from scripts._utils import is_etf_ticker

        assert is_etf_ticker("sh.600519") is False
        assert is_etf_ticker("sz.000001") is False
        assert is_etf_ticker("bj.920080") is False
        assert is_etf_ticker("600519") is False

    def test_normalize_ticker_key_etf_prefix(self) -> None:
        from scripts._utils import normalize_ticker_key

        assert normalize_ticker_key("510310") == "sh.510310"
        assert normalize_ticker_key("520700") == "sh.520700"
        assert normalize_ticker_key("551000") == "sh.551000"
        assert normalize_ticker_key("159915") == "sz.159915"


class TestExpectedTradingDay:
    """期望交易日计算（盘中 / 收盘后 / 周末）。"""

    def test_before_close_returns_previous_weekday(self) -> None:
        from datetime import datetime

        from scripts._utils import expected_trading_day

        # 2026-11-10 是周二，08:30 尚未开盘
        assert expected_trading_day(datetime(2026, 11, 10, 8, 30)) == "2026-11-09"

    def test_after_close_returns_same_day(self) -> None:
        from datetime import datetime

        from scripts._utils import expected_trading_day

        assert expected_trading_day(datetime(2026, 11, 10, 16, 0)) == "2026-11-10"

    def test_monday_before_close_returns_friday(self) -> None:
        from datetime import datetime

        from scripts._utils import expected_trading_day

        # 2026-11-09 是周一
        assert expected_trading_day(datetime(2026, 11, 9, 9, 0)) == "2026-11-06"

    def test_weekend_returns_friday(self) -> None:
        from datetime import datetime

        from scripts._utils import expected_trading_day

        assert expected_trading_day(datetime(2026, 11, 7, 18, 0)) == "2026-11-06"
        assert expected_trading_day(datetime(2026, 11, 8, 9, 0)) == "2026-11-06"
