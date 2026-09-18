"""tests for scripts.buffett_daily_analysis."""

from __future__ import annotations

from pathlib import Path

from scripts.buffett_daily_analysis import (
    _build_buffett_card,
    _build_stock_analysis_card,
    _parse_passing_stocks,
    _parse_total_screened,
    _ticker_to_code,
)

SAMPLE_RESULT_FILE_CONTENT = """巴菲特六闸门筛选结果（增强版）— 2026-09-18 11:42
通过五闸门（①~⑤）的股票共 18 只

  代码       名称          PE_TTM     PB    ROE%    毛利率%    负债率%   CAGR%  稳定性    现金流质量 数据备注
  -------- ---------- ------- ------ ------- ------- ------- -------  -------- ---------- ------------
  601336   新华保险           4.1   1.47    34.7     N/A    94.1    19.1  一般     优质 毛利率数据缺失; 金融股，豁免负债率门槛
  601838   成都银行           6.0   0.89    15.4     N/A    92.9     9.8  卓越     优质 毛利率数据缺失; 金融股，豁免负债率门槛
  002532   天山铝业           8.6   1.85    17.0    24.4    45.4    22.0  较弱     优质 -
  920208.BJ 青矩技术          10.0   1.90    18.5    48.2    32.8     5.4  数据不足   优质 -
  002001   新和成           11.3   2.33    21.9    44.7    27.7    23.2  卓越     优质 -

失败分布（共 4952 只）：
  ①PE=24.637178 PB=1.307425: 1 只
  ②ROE=6.76 扣非ROE=6.78: 1 只
"""


class TestTickerToCode:
    """_ticker_to_code helper tests."""

    def test_sh_ticker(self) -> None:
        assert _ticker_to_code("sh.600519") == "600519"

    def test_sz_ticker(self) -> None:
        assert _ticker_to_code("sz.000001") == "000001"

    def test_bj_ticker(self) -> None:
        assert _ticker_to_code("bj.920003") == "920003"

    def test_no_prefix(self) -> None:
        assert _ticker_to_code("600519") == "600519"

    def test_empty_string(self) -> None:
        assert _ticker_to_code("") == ""


class TestParsePassingStocks:
    """_parse_passing_stocks parsing tests."""

    def test_parses_mixed_sh_sz_bj(self, tmp_path: Path) -> None:
        result_file = tmp_path / "buffett_screen_20260918.txt"
        result_file.write_text(SAMPLE_RESULT_FILE_CONTENT, encoding="utf-8")
        passing = _parse_passing_stocks(result_file)
        assert len(passing) == 5
        codes = {code for code, _ in passing}
        assert "601336" in codes
        assert "601838" in codes
        assert "002532" in codes
        assert "920208.BJ" in codes
        assert "002001" in codes

    def test_stops_at_failure_distribution(self, tmp_path: Path) -> None:
        """Should not include failure lines as passing stocks."""
        content = SAMPLE_RESULT_FILE_CONTENT
        result_file = tmp_path / "screen.txt"
        result_file.write_text(content, encoding="utf-8")
        passing = _parse_passing_stocks(result_file)
        # Ensure none of the failure distribution lines are included
        for code, name in passing:
            assert "PE=" not in code
            assert "ROE=" not in code

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        result_file = tmp_path / "nonexistent.txt"
        passing = _parse_passing_stocks(result_file)
        assert passing == []

    def test_empty_file_returns_empty(self, tmp_path: Path) -> None:
        result_file = tmp_path / "empty.txt"
        result_file.write_text("", encoding="utf-8")
        passing = _parse_passing_stocks(result_file)
        assert passing == []

    def test_no_passing_stocks(self, tmp_path: Path) -> None:
        content = (
            "巴菲特六闸门筛选结果\n通过五闸门（①~⑤）的股票共 0 只\n\n失败分布（共 4966 只）：\n"
        )
        result_file = tmp_path / "screen.txt"
        result_file.write_text(content, encoding="utf-8")
        passing = _parse_passing_stocks(result_file)
        assert passing == []


class TestParseTotalScreened:
    """_parse_total_screened tests."""

    def test_parses_count(self, tmp_path: Path) -> None:
        result_file = tmp_path / "screen.txt"
        result_file.write_text("通过五闸门（①~⑤）的股票共 18 只\n", encoding="utf-8")
        assert _parse_total_screened(result_file) == 18

    def test_parses_large_count(self, tmp_path: Path) -> None:
        result_file = tmp_path / "screen.txt"
        result_file.write_text("通过五闸门（①~⑤）的股票共 4966 只\n", encoding="utf-8")
        assert _parse_total_screened(result_file) == 4966

    def test_missing_count_returns_zero(self, tmp_path: Path) -> None:
        result_file = tmp_path / "screen.txt"
        result_file.write_text("some other content\n", encoding="utf-8")
        assert _parse_total_screened(result_file) == 0

    def test_missing_file_returns_zero(self, tmp_path: Path) -> None:
        result_file = tmp_path / "nonexistent.txt"
        assert _parse_total_screened(result_file) == 0


class TestBuildBuffettCard:
    """_build_buffett_card tests."""

    def test_with_passing_stocks(self) -> None:
        passing = [("600519", "贵州茅台"), ("000001", "平安银行")]
        result = _build_buffett_card(passing, total_screened=5000, date_str="2026-09-18")
        assert "2026-09-18 巴菲特六闸门筛选结果" in result
        assert "总样本：5000 只 → 通过 **2** 只" in result
        assert "`600519` 贵州茅台" in result
        assert "`000001` 平安银行" in result

    def test_no_passing_stocks(self) -> None:
        result = _build_buffett_card([], total_screened=5000, date_str="2026-09-18")
        assert "⚠️ 本次无股票通过五闸门筛选" in result


class TestBuildStockAnalysisCard:
    """_build_stock_analysis_card tests."""

    def test_buy_score(self) -> None:
        result = {
            "score": 72.0,
            "close": 150.0,
            "change": 2.5,
            "trend": "强势上涨📈",
            "ma_signal": "多头排列✅",
            "vol_signal": "放量🔥",
            "ma5": 145.0,
            "ma10": 140.0,
            "ma20": 135.0,
        }
        card = _build_stock_analysis_card("600519", "贵州茅台", result, "2026-09-18")
        assert "🟢" in card
        assert "BUY" in card
        assert "72.0" in card
        assert "150.0" in card
        assert "+2.50%" in card

    def test_hold_score(self) -> None:
        result = {
            "score": 50.0,
            "close": 100.0,
            "change": -0.5,
            "trend": "横盘整理",
            "ma_signal": "震荡",
            "vol_signal": "正常",
            "ma5": 100.0,
            "ma10": 99.0,
            "ma20": 98.0,
        }
        card = _build_stock_analysis_card("000001", "平安银行", result, "2026-09-18")
        assert "🟡" in card
        assert "HOLD" in card
        assert "-0.50%" in card

    def test_sell_score(self) -> None:
        result = {
            "score": 10.0,
            "close": 50.0,
            "change": -3.2,
            "trend": "弱势下跌📉",
            "ma_signal": "空头排列❌",
            "vol_signal": "缩量📉",
            "ma5": 55.0,
            "ma10": 60.0,
            "ma20": 65.0,
        }
        card = _build_stock_analysis_card("601336", "新华保险", result, "2026-09-18")
        assert "🔴" in card
        assert "SELL" in card
        assert "-3.20%" in card

    def test_missing_optional_fields(self) -> None:
        result = {"score": 55.0, "trend": "横盘整理", "ma_signal": "偏多", "vol_signal": "正常"}
        card = _build_stock_analysis_card("600519", "贵州茅台", result, "2026-09-18")
        assert "?" in card  # missing close/change/ma fields show ?
