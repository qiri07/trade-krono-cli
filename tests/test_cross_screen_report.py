"""tests for scripts.cross_screen_report（流程交集 ∩ 六维选股）。"""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from scripts import cross_screen_report as csr
from scripts.combined_stock_models import StockCandidate


def _six_row(
    code: str = "600211",
    ticker: str = "sh.600211",
    composite: float = 74.7,
) -> dict[str, object]:
    """构造 1 条六维选股结果（字段与 six_dim_screen 产物一致）。"""
    return {
        "code": code,
        "name": "西藏药业",
        "ticker": ticker,
        "composite": composite,
        "logic": 83.8,
        "valuation": 71.9,
        "position": 74.8,
        "trend": 78.7,
        "holder": 38.8,
        "vol_price": 85.8,
        "metrics": {"pe": 12.8, "pb": 3.43, "peg": 0.35, "pe_pct": 15.2, "roe_avg": 27.9},
        "notes": {
            "position": "3年分位 52%",
            "trend": "多头排列",
            "holder": "股东户数环比 +4.5%",
            "vol_price": "5日/60日成交额 0.89×",
        },
    }


class TestLoadSixDim:
    """six_dim_screen JSON 解析。"""

    def test_parses_rows(self, tmp_path: Path) -> None:
        """正常文件解析为 SixDimRow，缺失维度记 0。"""
        path = tmp_path / "six_dim_screen_20261111.json"
        path.write_text(
            json.dumps({"date": "2026-11-11", "results": [_six_row()]}, ensure_ascii=False),
            encoding="utf-8",
        )
        rows = csr.load_six_dim(path)
        assert len(rows) == 1
        row = rows[0]
        assert (row.code, row.name, row.ticker) == ("600211", "西藏药业", "sh.600211")
        assert row.dims["logic"] == pytest.approx(83.8)
        assert row.metrics["pe"] == pytest.approx(12.8)
        assert row.notes["trend"] == "多头排列"

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        """文件缺失返回空列表（由 main 负责报错退出）。"""
        assert csr.load_six_dim(tmp_path / "none.json") == []

    def test_invalid_json_returns_empty(self, tmp_path: Path) -> None:
        """JSON 损坏返回空列表，不抛异常。"""
        path = tmp_path / "bad.json"
        path.write_text("{not json", encoding="utf-8")
        assert csr.load_six_dim(path) == []

    def test_skips_rows_without_code(self, tmp_path: Path) -> None:
        """无 code / ticker 的行被跳过。"""
        path = tmp_path / "x.json"
        path.write_text(
            json.dumps({"results": [{"name": "无代码"}, _six_row()]}),
            encoding="utf-8",
        )
        rows = csr.load_six_dim(path)
        assert [r.code for r in rows] == ["600211"]


class TestBuildCrossCard:
    """交集卡片正文。"""

    def _pool(self) -> dict[str, StockCandidate]:
        """构造 2 只流程交集候选（一只与六维重合）。"""
        cand = StockCandidate(
            code="sh.600211",
            hit_count=3,
            sources=["double_ma", "macd_ma", "czsc_bi_ma"],
        )
        cand.analysis = {
            "name": "西藏药业",
            "trend": "弱势下跌📉",
            "ma_signal": "震荡",
            "vol_signal": "正常",
            "score": 40.0,
            "valuation_conclusion": "PE分位8%，低估",
        }
        other = StockCandidate(code="sh.600519", hit_count=4, sources=["whitelist"])
        return {c.code: c for c in (cand, other)}

    def test_lists_hit_with_all_sections(self) -> None:
        """命中股票输出流程线、六维、估值、四类备注与技术面。"""
        metrics: dict[str, object] = {
            "pe": 12.8,
            "pb": 3.43,
            "peg": 0.35,
            "pe_pct": 15.2,
            "roe_avg": 27.9,
            "cfo_ratio": 1.07,
        }
        notes: dict[str, str] = {
            "position": "3年分位 52%",
            "trend": "多头排列",
            "holder": "股东户数环比 +4.5%",
            "vol_price": "5日/60日成交额 0.89×",
        }
        row = csr.SixDimRow(
            code="600211",
            name="西藏药业",
            ticker="sh.600211",
            composite=74.7,
            dims=dict.fromkeys(csr._SIX_DIM_DIMS, 70.0),
            metrics=metrics,
            notes=notes,
        )
        pool = self._pool()
        card = csr.build_cross_card(
            "2026-11-11", pool, [row], min_sources=3, details=pool
        )
        assert "交集：1 只" in card
        assert "西藏药业（sh.600211）- 流程内 3 条线" in card
        assert "双均线金叉+MACD+均线+缠论-笔向上+均线多头" in card
        assert "六维：综合 74.7" in card
        assert "PE=12.8（五年分位15%）" in card
        assert "技术面：40分" in card
        assert "估值结论：" in card

    def test_sorted_by_composite_desc(self) -> None:
        """多只命中按六维综合分降序。"""
        pool = {
            "sh.600211": StockCandidate(code="sh.600211", hit_count=3),
            "sh.600660": StockCandidate(code="sh.600660", hit_count=3),
        }
        rows = [
            csr.SixDimRow("600211", "A", "sh.600211", 70.0, {}, {}, {}),
            csr.SixDimRow("600660", "B", "sh.600660", 80.0, {}, {}, {}),
        ]
        card = csr.build_cross_card("2026-11-11", pool, rows, 3)
        assert card.index("B（sh.600660）") < card.index("A（sh.600211）")

    def test_empty_intersection_message(self) -> None:
        """无交集时给出明确提示。"""
        pool = {"sh.600519": StockCandidate(code="sh.600519", hit_count=4)}
        rows = [csr.SixDimRow("600211", "A", "sh.600211", 70.0, {}, {}, {})]
        card = csr.build_cross_card("2026-11-11", pool, rows, 3)
        assert "无交集股票" in card

    def test_matches_by_bare_code_when_ticker_missing(self) -> None:
        """六维行无 ticker 时可用裸代码与流程池匹配。"""
        pool = {"sh.600211": StockCandidate(code="sh.600211", hit_count=3)}
        rows = [csr.SixDimRow("600211", "A", "600211", 70.0, {}, {}, {})]
        card = csr.build_cross_card("2026-11-11", pool, rows, 3)
        assert "交集：1 只" in card


class TestMain:
    """main 端到端（patch 数据读取，验证落盘与推送开关）。"""

    def _argv(self, out: Path, *extra: str) -> list[str]:
        """构造 argv。"""
        return [
            "cross_screen_report.py",
            "--date",
            "2026-11-11",
            "--out",
            str(out),
            *extra,
        ]

    def test_no_push_writes_card(self, tmp_path: Path) -> None:
        """--no-push 只写卡片，不调用飞书。"""
        rows = [
            csr.SixDimRow("600211", "西藏药业", "sh.600211", 74.7, {}, {}, {})
        ]
        out = tmp_path / "card.txt"
        with (
            patch.object(
                csr,
                "collect_source_sets",
                return_value={
                    "whitelist": {"600211"},
                    "resonance": {"600211"},
                    "multi_head": {"600211"},
                },
            ),
            patch.object(csr, "load_six_dim", return_value=rows),
            patch.object(csr, "load_analyzed_details", return_value={}),
            patch.object(csr, "push_feishu") as mock_push,
            patch.object(sys, "argv", self._argv(out, "--no-push")),
        ):
            csr.main()
        assert mock_push.called is False
        text = out.read_text(encoding="utf-8")
        assert "交集：1 只" in text
        assert "sh.600211" in text

    def test_push_called_by_default(self, tmp_path: Path) -> None:
        """默认推送一次。"""
        rows = [
            csr.SixDimRow("600211", "西藏药业", "sh.600211", 74.7, {}, {}, {})
        ]
        with (
            patch.object(
                csr,
                "collect_source_sets",
                return_value={"whitelist": {"600211"}},
            ),
            patch.object(csr, "load_six_dim", return_value=rows),
            patch.object(csr, "load_analyzed_details", return_value={}),
            patch.object(csr, "push_feishu", return_value=True) as mock_push,
            patch.object(sys, "argv", self._argv(tmp_path / "c.txt", "--min-sources", "1")),
        ):
            csr.main()
        assert mock_push.call_count == 1
        assert mock_push.call_args.args[0] == "text"

    def test_missing_six_dim_exits(self, tmp_path: Path) -> None:
        """六维结果缺失时以非零码退出。"""
        with (
            patch.object(csr, "collect_source_sets", return_value={}),
            patch.object(csr, "load_six_dim", return_value=[]),
            patch.object(csr, "push_feishu") as mock_push,
            patch.object(sys, "argv", self._argv(tmp_path / "c.txt")),
            pytest.raises(SystemExit) as exc,
        ):
            csr.main()
        assert exc.value.code == 1
        assert mock_push.called is False


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
