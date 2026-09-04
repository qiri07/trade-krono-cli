"""测试 tests/buffett_screening.py — 筛选业务逻辑。

覆盖：_safe_float / screen_one 六闸门 / evaluate_profitability_stability /
      evaluate_cash_quality / StockMetrics / write_result_file /
      _verify_pe_percentile（LLM 辅助 Gate ⑥）。
（API 调用函数通过 mock 覆盖）
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

from tests.buffett_screening import (
    StockMetrics,
    batch_valuations,
    evaluate_cash_quality,
    evaluate_profitability_stability,
    fetch_cfo_ratio_history,
    fetch_roe_history,
    screen_one,
    write_result_file,
)

# ── _safe_float ────────────────────────────────────────────────────────────────


class TestSafeFloat:
    def test_none(self) -> None:
        from tests.buffett_screening import _safe_float

        assert _safe_float(None) is None

    def test_valid_int(self) -> None:
        from tests.buffett_screening import _safe_float

        assert _safe_float(42) == 42.0

    def test_valid_float(self) -> None:
        from tests.buffett_screening import _safe_float

        assert _safe_float(15.5) == 15.5

    def test_string_number(self) -> None:
        from tests.buffett_screening import _safe_float

        assert _safe_float("23.7") == 23.7

    def test_nan_returns_none(self) -> None:
        from tests.buffett_screening import _safe_float

        assert _safe_float(float("nan")) is None

    def test_inf_returns_none(self) -> None:
        from tests.buffett_screening import _safe_float

        assert _safe_float(float("inf")) is None
        assert _safe_float(float("-inf")) is None

    def test_empty_string_returns_none(self) -> None:
        from tests.buffett_screening import _safe_float

        assert _safe_float("") is None

    def test_invalid_string_returns_none(self) -> None:
        from tests.buffett_screening import _safe_float

        assert _safe_float("abc") is None


# ── screen_one 五闸门 ──────────────────────────────────────────────────────────


def _make_val(pe: float | None, pb: float | None) -> dict:
    return {"pe_ttm": pe, "pb_mrq": pb}


def _make_fin(roe: float | None, roe_excl: float | None, debt: float | None) -> dict:
    return {"roe": roe, "roe_excl": roe_excl, "debt_ratio": debt}


class TestScreenOne:
    """五闸门筛选逻辑单元测试。"""

    def test_gate1_pe_too_high(self) -> None:
        val = _make_val(pe=20.0, pb=2.0)
        m = screen_one("600519.SH", "贵州茅台", val, {}, None, [], None)
        assert m.gate_fail.startswith("①")
        assert m.pe_ttm == 20.0

    def test_gate1_pb_too_high(self) -> None:
        val = _make_val(pe=10.0, pb=5.0)
        m = screen_one("600519.SH", "贵州茅台", val, {}, None, [], None)
        assert m.gate_fail.startswith("①")

    def test_gate1_both_pass(self) -> None:
        """PE/PB 都在阈值内，应继续到下一闸门。"""
        val = _make_val(pe=15.0, pb=2.5)
        fin = _make_fin(roe=20.0, roe_excl=18.0, debt=30.0)
        m = screen_one(
            "600519.SH",
            "贵州茅台",
            val,
            fin,
            None,
            [
                {"fiscal_year": 2024, "parent_holder_net_profit": 100},
                {"fiscal_year": 2023, "parent_holder_net_profit": 80},
            ],
            5.0,
        )
        assert m.gate_fail.startswith("⑥")  # 通过了所有闸门

    def test_gate1_no_pe(self) -> None:
        val = _make_val(pe=None, pb=2.0)
        m = screen_one("600519.SH", "贵州茅台", val, {}, None, [], None)
        assert "无估值数据" in m.gate_fail

    def test_gate1_negative_pe(self) -> None:
        """负 PE（亏损股）应被闸门①拦截。"""
        val = _make_val(pe=-5.0, pb=1.5)
        m = screen_one("600519.SH", "贵州茅台", val, {}, None, [], None)
        assert m.gate_fail.startswith("①")

    def test_gate2_roe_too_low(self) -> None:
        val = _make_val(pe=10.0, pb=2.0)
        fin = _make_fin(roe=10.0, roe_excl=8.0, debt=None)
        m = screen_one("600519.SH", "贵州茅台", val, fin, None, [], None)
        assert m.gate_fail.startswith("②")

    def test_gate2_roe_excl_too_low(self) -> None:
        """扣非ROE < 12% 应失败，即使 ROE > 15%。"""
        val = _make_val(pe=10.0, pb=2.0)
        fin = _make_fin(roe=20.0, roe_excl=10.0, debt=None)
        m = screen_one("600519.SH", "贵州茅台", val, fin, None, [], None)
        assert m.gate_fail.startswith("②")

    def test_gate2_roe_missing(self) -> None:
        val = _make_val(pe=10.0, pb=2.0)
        fin = _make_fin(roe=None, roe_excl=None, debt=None)
        m = screen_one("600519.SH", "贵州茅台", val, fin, None, [], None)
        assert m.gate_fail.startswith("②")

    def test_gate3_debt_too_high(self) -> None:
        val = _make_val(pe=10.0, pb=2.0)
        fin = _make_fin(roe=20.0, roe_excl=18.0, debt=60.0)
        m = screen_one("600519.SH", "贵州茅台", val, fin, None, [], None)
        assert m.gate_fail.startswith("③")

    def test_gate3_debt_boundary(self) -> None:
        """负债率恰好 50% 应失败（< 50%，不含等于）。"""
        val = _make_val(pe=10.0, pb=2.0)
        fin = _make_fin(roe=20.0, roe_excl=18.0, debt=50.0)
        m = screen_one("600519.SH", "贵州茅台", val, fin, None, [], None)
        assert m.gate_fail.startswith("③")

    def test_gate3_debt_ok(self) -> None:
        fin = _make_fin(roe=20.0, roe_excl=18.0, debt=49.9)
        val = _make_val(pe=10.0, pb=2.0)
        m = screen_one("600519.SH", "贵州茅台", val, fin, None, [], None)
        # 继续到闸门⑤（CAGR），因无利润表数据，会失败在⑤
        assert m.gate_fail.startswith("⑤")

    def test_gate5_no_income_data(self) -> None:
        """无利润表数据 → CAGR=None → 失败。"""
        val = _make_val(pe=10.0, pb=2.0)
        fin = _make_fin(roe=20.0, roe_excl=18.0, debt=30.0)
        m = screen_one("600519.SH", "贵州茅台", val, fin, None, [], None)
        assert m.gate_fail.startswith("⑤")

    def test_gate5_negative_cagr(self) -> None:
        """利润下降 → CAGR<0 → 失败。"""
        val = _make_val(pe=10.0, pb=2.0)
        fin = _make_fin(roe=20.0, roe_excl=18.0, debt=30.0)
        items = [
            {"fiscal_year": 2024, "parent_holder_net_profit": 80.0},
            {"fiscal_year": 2023, "parent_holder_net_profit": 100.0},
        ]
        m = screen_one("600519.SH", "贵州茅台", val, fin, None, items, 5.0)
        assert m.gate_fail.startswith("⑤")

    def test_gate5_positive_cagr(self) -> None:
        """利润增长 → 通过闸门⑤，到闸门④（CFO）。"""
        val = _make_val(pe=10.0, pb=2.0)
        fin = _make_fin(roe=20.0, roe_excl=18.0, debt=30.0)
        items = [
            {"fiscal_year": 2024, "parent_holder_net_profit": 120.0},
            {"fiscal_year": 2023, "parent_holder_net_profit": 100.0},
        ]
        m = screen_one("600519.SH", "贵州茅台", val, fin, None, items, 5.0)
        assert m.gate_fail.startswith("⑥")  # 全部通过

    def test_gate4_cfo_negative(self) -> None:
        """CFO < 0 → 失败。"""
        val = _make_val(pe=10.0, pb=2.0)
        fin = _make_fin(roe=20.0, roe_excl=18.0, debt=30.0)
        items = [
            {"fiscal_year": 2024, "parent_holder_net_profit": 120.0},
            {"fiscal_year": 2023, "parent_holder_net_profit": 100.0},
        ]
        m = screen_one("600519.SH", "贵州茅台", val, fin, None, items, -5.0)
        assert m.gate_fail.startswith("④")

    def test_gate4_cfo_zero(self) -> None:
        """CFO = 0 → 失败（需 > 0）。"""
        val = _make_val(pe=10.0, pb=2.0)
        fin = _make_fin(roe=20.0, roe_excl=18.0, debt=30.0)
        items = [
            {"fiscal_year": 2024, "parent_holder_net_profit": 120.0},
            {"fiscal_year": 2023, "parent_holder_net_profit": 100.0},
        ]
        m = screen_one("600519.SH", "贵州茅台", val, fin, None, items, 0.0)
        assert m.gate_fail.startswith("④")

    def test_gate4_cfo_none(self) -> None:
        """CFO = None → 失败。"""
        val = _make_val(pe=10.0, pb=2.0)
        fin = _make_fin(roe=20.0, roe_excl=18.0, debt=30.0)
        items = [
            {"fiscal_year": 2024, "parent_holder_net_profit": 120.0},
            {"fiscal_year": 2023, "parent_holder_net_profit": 100.0},
        ]
        m = screen_one("600519.SH", "贵州茅台", val, fin, None, items, None)
        assert m.gate_fail.startswith("④")

    def test_all_gates_pass(self) -> None:
        """完整通过五闸门，gate_fail 应为 ⑥ 开头。"""
        val = _make_val(pe=12.0, pb=2.0)
        fin = _make_fin(roe=22.0, roe_excl=20.0, debt=35.0)
        items = [
            {"fiscal_year": 2024, "parent_holder_net_profit": 150.0},
            {"fiscal_year": 2023, "parent_holder_net_profit": 100.0},
        ]
        m = screen_one("600519.SH", "贵州茅台", val, fin, None, items, 10.0)
        assert m.gate_fail.startswith("⑥")
        assert m.cfo_ok is True
        assert m.pe_ttm == 12.0
        assert m.roe == 22.0
        assert m.cagr_3y is not None
        assert m.cagr_3y > 0


# ── evaluate_profitability_stability ───────────────────────────────────────────


class TestEvaluateProfitabilityStability:
    def test_insufficient_data(self) -> None:
        assert evaluate_profitability_stability([]) == "数据不足"
        assert evaluate_profitability_stability([{"year": 2024, "roe": 20.0}]) == "数据不足"

    def test_excellent(self) -> None:
        """≥80% 年份 ROE≥15% 且标准差 < 5 → 卓越。"""
        history = [{"year": y, "roe": 20.0} for y in range(2016, 2026)]
        assert evaluate_profitability_stability(history) == "卓越"

    def test_excellent_mixed(self) -> None:
        """9/10 年达标，标准差小 → 卓越。"""
        history = [{"year": y, "roe": 18.0 if y != 2020 else 10.0} for y in range(2016, 2026)]
        assert evaluate_profitability_stability(history) == "卓越"

    def test_优秀(self) -> None:
        """≥60% 年份达标，标准差 < 8 → 优秀。"""
        history = [{"year": y, "roe": 16.0} for y in range(2016, 2026)]
        # 全部达标，std_dev=0 → 卓越（更高等级）
        assert evaluate_profitability_stability(history) == "卓越"

    def test_good(self) -> None:
        """50%-60% 年份达标 → 良好。"""
        # 5/10 年 ROE≥15%, 5/10 年 < 15%
        history = [{"year": y, "roe": 20.0 if y % 2 == 0 else 10.0} for y in range(2016, 2026)]
        assert evaluate_profitability_stability(history) == "良好"

    def test_weak(self) -> None:
        """< 40% 年份达标 → 较弱。"""
        history = [
            {"year": y, "roe": 20.0 if y in (2020, 2021, 2022) else 5.0} for y in range(2016, 2026)
        ]
        assert evaluate_profitability_stability(history) == "较弱"

    def test_high_volatility(self) -> None:
        """ROE 波动大 → 降级。"""
        history = [{"year": y, "roe": 30.0 if y % 2 == 0 else 5.0} for y in range(2016, 2026)]
        result = evaluate_profitability_stability(history)
        # 50% 年份达标但标准差大，应降级
        assert result in ("良好", "一般", "较弱")


# ── evaluate_cash_quality ──────────────────────────────────────────────────────


class TestEvaluateCashQuality:
    def test_insufficient_data(self) -> None:
        assert evaluate_cash_quality([]) == "数据不足"

    def test_excellent(self) -> None:
        """≥80% 年份 ratio≥0.8 且平均 ratio ≥ 0.9 → 优质。"""
        history = [{"year": y, "ratio": 0.95} for y in range(2021, 2026)]
        assert evaluate_cash_quality(history) == "优质"

    def test_good(self) -> None:
        """平均 ratio ≥ 0.7 且 ≥60% 年份达标 → 良好。"""
        # 4/5 年 ratio=0.85（≥0.8），1/5 年 ratio=0.6 → 80% 达标，avg=0.8
        history = [{"year": y, "ratio": 0.85} for y in range(2021, 2025)] + [
            {"year": 2025, "ratio": 0.6}
        ]
        # avg = (0.85*4+0.6)/5 = 4.0/5 = 0.8，≥0.8 年份 4/5=80% ≥ 60%
        assert evaluate_cash_quality(history) == "良好"

    def test_average(self) -> None:
        """平均 ratio ≥ 0.5 → 一般。"""
        history = [{"year": y, "ratio": 0.55} for y in range(2021, 2026)]
        assert evaluate_cash_quality(history) == "一般"

    def test_poor(self) -> None:
        """平均 ratio < 0.5 → 较差。"""
        history = [{"year": y, "ratio": 0.3} for y in range(2021, 2026)]
        assert evaluate_cash_quality(history) == "较差"

    def test_mixed_ratios(self) -> None:
        history = [
            {"year": 2021, "ratio": 1.2},
            {"year": 2022, "ratio": 0.9},
            {"year": 2023, "ratio": 0.3},
            {"year": 2024, "ratio": 0.2},
            {"year": 2025, "ratio": 0.1},
        ]
        # avg = 0.54, ratio≥0.8 的比例 = 2/5=40% → avg≥0.5 → 一般
        assert evaluate_cash_quality(history) == "一般"


# ── StockMetrics 数据类 ────────────────────────────────────────────────────────


class TestStockMetrics:
    def test_defaults(self) -> None:
        m = StockMetrics(
            ticker="600519",
            thscode="600519.SH",
            name="贵州茅台",
            pe_ttm=15.0,
            pb=3.0,
            roe=20.0,
            roe_excl=18.0,
            debt_ratio=30.0,
            cagr_3y=10.0,
            cfo_ok=True,
        )
        assert m.gate_fail == ""
        assert m.roe_10y is None
        assert m.cfo_ratio_5y is None
        assert m.profitability_stability == ""
        assert m.cash_quality_rating == ""
        assert m.cagr_is_net_profit is True

    def test_gate_fail_filled(self) -> None:
        m = StockMetrics(
            ticker="",
            thscode="600519.SH",
            name="贵州茅台",
            pe_ttm=None,
            pb=None,
            roe=None,
            roe_excl=None,
            debt_ratio=None,
            cagr_3y=None,
            cfo_ok=False,
            gate_fail="①PE=None PB=2.0",
        )
        assert m.gate_fail == "①PE=None PB=2.0"


# ── API 函数（mock） ───────────────────────────────────────────────────────────


class TestFetchFunctions:
    @patch("tests.buffett_screening._api_get")
    def test_fetch_roe_history_hits_cache(self, mock_api) -> None:
        cache = {"roe_hist_600519": [{"year": 2024, "roe": 20.0}]}
        history, new_entry = fetch_roe_history("600519", None, cache)
        assert history == [{"year": 2024, "roe": 20.0}]
        assert new_entry is None
        mock_api.assert_not_called()

    @patch("tests.buffett_screening._api_get")
    def test_fetch_cfo_ratio_hits_cache(self, mock_api) -> None:
        cache = {"cfo_ratio_600519": [{"year": 2024, "ratio": 0.9}]}
        history, new_entry = fetch_cfo_ratio_history("600519", None, cache)
        assert history == [{"year": 2024, "ratio": 0.9}]
        assert new_entry is None
        mock_api.assert_not_called()

    @patch("tests.buffett_screening._api_get")
    def test_fetch_roe_history_api_miss(self, mock_api) -> None:
        """API 未命中时应发起请求。"""
        mock_api.return_value = {"code": 0, "data": {"abilities": []}}
        cache: dict = {}
        history, new_entry = fetch_roe_history("600519", None, cache)
        assert history == []
        assert new_entry is None  # 空历史不写入缓存
        assert mock_api.call_count == 10  # 10 年

    @patch("tests.buffett_screening._api_get")
    def test_batch_valuations_cache_hit(self, mock_api) -> None:
        # cache key 使用 sorted batch 的 thscode
        cache = {
            "val_batch_000858.SZ,600519.SH": {
                "600519.SH": {"pe_ttm": 15.0},
                "000858.SZ": {"pe_ttm": 12.0},
            }
        }
        result, new_entries = batch_valuations(["600519.SH", "000858.SZ"], None, cache)
        assert result["600519.SH"] == {"pe_ttm": 15.0}
        assert result["000858.SZ"] == {"pe_ttm": 12.0}
        assert len(new_entries) == 0
        mock_api.assert_not_called()


# ── write_result_file ──────────────────────────────────────────────────────────


class TestWriteResultFile:
    def test_basic_output(self, tmp_path: Path) -> None:
        out = tmp_path / "result.txt"
        pass_list = [
            StockMetrics(
                ticker="600519",
                thscode="600519.SH",
                name="贵州茅台",
                pe_ttm=15.0,
                pb=3.0,
                roe=20.0,
                roe_excl=18.0,
                debt_ratio=30.0,
                cagr_3y=10.0,
                cfo_ok=True,
                profitability_stability="卓越",
                cash_quality_rating="优质",
            ),
            StockMetrics(
                ticker="000858",
                thscode="000858.SZ",
                name="五粮液",
                pe_ttm=12.0,
                pb=2.0,
                roe=25.0,
                roe_excl=23.0,
                debt_ratio=20.0,
                cagr_3y=15.0,
                cfo_ok=True,
                profitability_stability="优秀",
                cash_quality_rating="良好",
            ),
        ]
        fail_list = [
            StockMetrics(
                ticker="",
                thscode="999999.SH",
                name="失败股",
                pe_ttm=50.0,
                pb=None,
                roe=None,
                roe_excl=None,
                debt_ratio=None,
                cagr_3y=None,
                cfo_ok=False,
                gate_fail="①PE=50.0 PB=None",
            ),
        ]
        write_result_file(str(out), pass_list, fail_list)
        content = out.read_text(encoding="utf-8")
        assert "贵州茅台" in content
        assert "五粮液" in content
        assert "600519" in content
        assert "000858" in content
        assert "失败分布" in content
        assert "①PE=50.0" in content

    def test_empty_results(self, tmp_path: Path) -> None:
        out = tmp_path / "empty.txt"
        write_result_file(str(out), [], [])
        content = out.read_text(encoding="utf-8")
        assert "共 0 只" in content

    def test_sorted_by_pe_then_roe(self, tmp_path: Path) -> None:
        """结果应按 PE 升序、ROE 降序排列。"""
        pass_list = [
            StockMetrics(
                ticker="A",
                thscode="A.SH",
                name="A",
                pe_ttm=20.0,
                pb=2.0,
                roe=18.0,
                roe_excl=16.0,
                debt_ratio=30.0,
                cagr_3y=10.0,
                cfo_ok=True,
            ),
            StockMetrics(
                ticker="B",
                thscode="B.SH",
                name="B",
                pe_ttm=10.0,
                pb=1.5,
                roe=25.0,
                roe_excl=22.0,
                debt_ratio=20.0,
                cagr_3y=15.0,
                cfo_ok=True,
            ),
            StockMetrics(
                ticker="C",
                thscode="C.SH",
                name="C",
                pe_ttm=10.0,
                pb=1.8,
                roe=20.0,
                roe_excl=18.0,
                debt_ratio=25.0,
                cagr_3y=8.0,
                cfo_ok=True,
            ),
        ]
        write_result_file(str(tmp_path / "r.txt"), pass_list, [])
        content = (tmp_path / "r.txt").read_text(encoding="utf-8")
        # B (PE=10, ROE=25) 应在 C (PE=10, ROE=20) 之前
        pos_b = content.find("B")
        pos_c = content.find("C")
        assert pos_b < pos_c


# ── _verify_pe_percentile ───────────────────────────────────────────────────────


class TestVerifyPePercentile:
    """测试 _verify_pe_percentile AI 辅助 PE 历史分位判断。"""

    def test_no_llm_key_returns_default_pass(self) -> None:
        """_LLM_AVAILABLE=False 时默认通过。"""
        from tests.buffett_screening import _verify_pe_percentile

        with patch("tests.buffett_screening._LLM_AVAILABLE", False):
            is_pass, reason = _verify_pe_percentile("600004", "白云机场", 13.83)
        assert is_pass is True
        assert "LLM 未配置" in reason

    def test_llm_available_but_no_key_returns_default_pass(self) -> None:
        """DEEPSEEK_API_KEY 为空字符串时默认通过。"""
        from tests.buffett_screening import _verify_pe_percentile

        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "", "OPENAI_API_KEY": ""}, clear=True):
            is_pass, reason = _verify_pe_percentile("600004", "白云机场", 13.83)
        assert is_pass is True

    @patch("tests.buffett_screening._LLM_AVAILABLE", True)
    @patch("tests.buffett_screening.logger")
    def test_ai_declines_passes_false(self, mock_logger) -> None:
        """AI 判断为非低估区间时返回 False。"""
        from tests.buffett_screening import _verify_pe_percentile

        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="否\n理由：估值处于中位"))]
        with patch("tests.buffett_screening.OpenAI") as mock_openai:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_openai.return_value = mock_client
            with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-test"}):
                is_pass, reason = _verify_pe_percentile("603721", "中广天择", 103.38)
        assert is_pass is False
        assert "AI核实❌" in reason

    @patch("tests.buffett_screening._LLM_AVAILABLE", True)
    def test_ai_accepts_returns_true(self) -> None:
        """AI 判断为低估区间时返回 True。"""
        from tests.buffett_screening import _verify_pe_percentile

        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="是\n理由：处于历史低位"))]
        with patch("tests.buffett_screening.OpenAI") as mock_openai:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_openai.return_value = mock_client
            with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-test"}):
                is_pass, reason = _verify_pe_percentile("600004", "白云机场", 13.83)
        assert is_pass is True
        assert "AI核实✅" in reason

    @patch("tests.buffett_screening._LLM_AVAILABLE", True)
    def test_ai_undetermined_defaults_to_pass(self) -> None:
        """AI 无法确定时默认通过（安全降级）。"""
        from tests.buffett_screening import _verify_pe_percentile

        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="未知\n理由：数据不足"))]
        with patch("tests.buffett_screening.OpenAI") as mock_openai:
            mock_client = MagicMock()
            mock_client.chat.completions.create.return_value = mock_response
            mock_openai.return_value = mock_client
            with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-test"}):
                is_pass, reason = _verify_pe_percentile("600004", "白云机场", 13.83)
        assert is_pass is True
        assert "AI核实⚠️" in reason

    @patch("tests.buffett_screening._LLM_AVAILABLE", True)
    def test_api_error_defaults_to_pass(self) -> None:
        """API 异常时默认通过（安全降级）。"""
        from tests.buffett_screening import _verify_pe_percentile

        with patch("tests.buffett_screening.OpenAI") as mock_openai:
            mock_openai.side_effect = RuntimeError("network error")
            with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "sk-test"}):
                is_pass, reason = _verify_pe_percentile("600004", "白云机场", 13.83)
        assert is_pass is True
        assert "AI核实⚠️" in reason
