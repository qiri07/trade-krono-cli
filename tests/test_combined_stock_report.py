"""tests for scripts.combined_stock_report（交集重放：卡片生成 + 可选推送）。"""

from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from scripts import combined_stock_report as csr
from scripts.combined_stock_models import StockCandidate


class TestNormalizeCode:
    """代码归一化（统一带前缀 ticker，剔除北交所 / 非法代码）。"""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("600519", "sh.600519"),
            ("000858", "sz.000858"),
            ("sz.002911", "sz.002911"),
            ("sh.588000", "sh.588000"),
            ("507", None),
            ("", None),
        ],
    )
    def test_normalize(self, raw: str, expected: str | None) -> None:
        """裸代码补前缀；已带前缀保持；非法长度返回 None。"""
        assert csr._normalize_code(raw) == expected

    def test_bse_returns_none(self) -> None:
        """北交所代码（920 开头）应剔除。"""
        assert csr._normalize_code("920001") is None
        assert csr._normalize_code("bj.920001") is None


class TestNormalizeSourceSets:
    """来源集合归一化（合并裸代码 / 带前缀两种写法）。"""

    def test_merges_prefix_and_bare_variants(self) -> None:
        """sh.600519 与 600519 应合并为同一只股票。"""
        source_sets = {
            "whitelist": {"600519"},
            "intraday": {"sh.600519"},
        }
        normalized = csr.normalize_source_sets(source_sets)
        assert normalized == {"whitelist": {"sh.600519"}, "intraday": {"sh.600519"}}

    def test_drops_bse(self) -> None:
        """北交所代码在归一化阶段剔除。"""
        normalized = csr.normalize_source_sets({"whitelist": {"920001", "600519"}})
        assert normalized == {"whitelist": {"sh.600519"}}


class TestLoadAnalyzedDetails:
    """明细 JSON 解析（双键索引 + Kronos / 回测还原）。"""

    def _write(self, tmp_path: Path) -> Path:
        """写出 1 只含 kronos + backtest 的 analyzed 明细。"""
        payload = {
            "date": "2026-11-11",
            "min_sources": 3,
            "analyzed": [
                {
                    "code": "002911",
                    "hit_count": 6,
                    "sources": ["resonance", "czsc_bi_ma"],
                    "name": "佛燃能源",
                    "trend": "强势上涨📈",
                    "ma_signal": "多头排列✅",
                    "vol_signal": "正常",
                    "score": 85.0,
                    "pe_ttm": 15.6,
                    "pb": 4.07,
                    "valuation_score": 82.9,
                    "valuation_conclusion": "低估",
                    "kronos": {
                        "direction": "UP",
                        "expected_change_pct": 3.2,
                        "confidence_low": -1.0,
                        "confidence_high": 7.0,
                        "horizon": 5,
                    },
                    "backtest": {
                        "total_return_pct": 9.9,
                        "sharpe_ratio": 1.5,
                        "max_drawdown_pct": -5.0,
                        "win_rate_pct": 55.0,
                        "n_trades": 4,
                        "n_signals": 10,
                        "horizon_days": 5,
                        "window_days": 90,
                        "available": True,
                    },
                }
            ],
        }
        path = tmp_path / "combined_intersection_20261111.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        return path

    def test_dual_key_index_with_views(self, tmp_path: Path) -> None:
        """裸代码与带前缀 ticker 双键可查，视图数据完整。"""
        details = csr.load_analyzed_details(self._write(tmp_path), "2026-11-11")
        cand = details["sz.002911"]
        assert details["002911"] is cand
        assert cand.tech.name == "佛燃能源"
        assert cand.fund.pe_ttm == pytest.approx(15.6)
        assert cand.kronos_view.direction == "UP"
        assert cand.backtest is not None and cand.backtest.available

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        """明细文件缺失时返回空字典，不抛异常。"""
        assert csr.load_analyzed_details(tmp_path / "none.json", "2026-11-11") == {}


class TestMain:
    """main 端到端（patch 数据采集，验证卡片落盘与推送开关）。"""

    def test_no_push_writes_card(self, tmp_path: Path) -> None:
        """--no-push 只写卡片文件，不调用飞书推送。"""
        source_sets = {
            "whitelist": {"600519", "000858"},
            "resonance": {"600519"},
            "multi_head": {"600519"},
        }
        out = tmp_path / "card.txt"
        argv = [
            "combined_stock_report.py",
            "--date",
            "2026-11-11",
            "--min-sources",
            "3",
            "--no-push",
            "--out",
            str(out),
            "--detail-json",
            str(tmp_path / "missing.json"),
        ]
        with (
            patch.object(csr, "collect_source_sets", return_value=source_sets),
            patch.object(csr, "push_feishu") as mock_push,
            patch.object(sys, "argv", argv),
        ):
            csr.main()
        assert mock_push.called is False
        text = out.read_text(encoding="utf-8")
        assert "命中 ≥3 条线共 1 只" in text
        assert "sh.600519" in text

    def test_push_called_by_default(self, tmp_path: Path) -> None:
        """默认推送一次汇总卡片。"""
        argv = [
            "combined_stock_report.py",
            "--date",
            "2026-11-11",
            "--out",
            str(tmp_path / "card.txt"),
            "--detail-json",
            str(tmp_path / "missing.json"),
        ]
        with (
            patch.object(
                csr,
                "collect_source_sets",
                return_value={"whitelist": {"600519"}, "resonance": {"600519"}},
            ),
            patch.object(csr, "push_feishu", return_value=True) as mock_push,
            patch.object(sys, "argv", argv),
        ):
            csr.main()
        assert mock_push.call_count == 1
        assert mock_push.call_args.args[0] == "text"


class TestDetailFallback:
    """analyzed_map 缺失时前 10 名不应崩溃（只显示名称 + 交集次数）。"""

    def test_card_without_details(self) -> None:
        """无明细映射时卡片仍含每条候选行。"""
        cands = [
            StockCandidate(code="sh.600519", hit_count=5, sources=["whitelist"])
        ]
        card = csr._build_combined_card(cands, "2026-11-11", {"whitelist": {"sh.600519"}})
        assert "sh.600519" in card


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
