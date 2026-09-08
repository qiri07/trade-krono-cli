"""测试 parse_top3 — 从 results.json 提取 Top 3 推荐摘要。"""

from __future__ import annotations

import json
from pathlib import Path

from scripts.parse_top3 import parse_top3


class TestParseTop3:
    """parse_top3 函数测试。"""

    def test_missing_file_returns_empty(self, tmp_path: Path) -> None:
        """文件不存在时返回空字符串。"""
        assert parse_top3(tmp_path / "nonexistent.json") == ""

    def test_invalid_json_returns_empty(self, tmp_path: Path) -> None:
        """JSON 格式错误时返回空字符串。"""
        f = tmp_path / "results.json"
        f.write_text("{invalid json", encoding="utf-8")
        assert parse_top3(f) == ""

    def test_empty_list_returns_empty(self, tmp_path: Path) -> None:
        """结果为空列表时返回空字符串。"""
        f = tmp_path / "results.json"
        f.write_text("[]", encoding="utf-8")
        assert parse_top3(f) == ""

    def test_dict_format_no_results_key(self, tmp_path: Path) -> None:
        """dict 格式但无 results 键时返回空字符串。"""
        f = tmp_path / "results.json"
        f.write_text(json.dumps({"project": "trade-krono-cli"}), encoding="utf-8")
        assert parse_top3(f) == ""

    def test_single_item(self, tmp_path: Path) -> None:
        """单条结果：ticker:信号 分数。"""
        f = tmp_path / "results.json"
        data = {
            "project": "trade-krono-cli",
            "results": [{"ticker": "sh.600519", "ta_signal": "BUY", "ranking_score": 0.85}],
        }
        f.write_text(json.dumps(data), encoding="utf-8")
        result = parse_top3(f)
        assert "600519" in result
        assert "BUY" in result
        assert "0.85" in result

    def test_three_items_joined_by_slash(self, tmp_path: Path) -> None:
        """三条结果用 '  /  ' 分隔。"""
        f = tmp_path / "results.json"
        items = [
            {"ticker": "sh.600519", "ta_signal": "BUY", "ranking_score": 0.90},
            {"ticker": "sz.000858", "ta_signal": "HOLD", "ranking_score": 0.75},
            {"ticker": "sh.601318", "ta_signal": "SELL", "ranking_score": 0.60},
        ]
        f.write_text(json.dumps({"results": items}), encoding="utf-8")
        result = parse_top3(f)
        parts = result.split("  /  ")
        assert len(parts) == 3
        assert "600519" in parts[0]
        assert "000858" in parts[1]
        assert "601318" in parts[2]

    def test_more_than_three_uses_only_top_three(self, tmp_path: Path) -> None:
        """超过3条时只取前3条。"""
        f = tmp_path / "results.json"
        items = [
            {"ticker": f"sh.{i:06d}", "ta_signal": "BUY", "ranking_score": float(i)}
            for i in range(5)
        ]
        f.write_text(json.dumps({"results": items}), encoding="utf-8")
        result = parse_top3(f)
        parts = result.split("  /  ")
        assert len(parts) == 3
        assert "000000" in parts[0]
        assert "000001" in parts[1]
        assert "000002" in parts[2]

    def test_legacy_list_format(self, tmp_path: Path) -> None:
        """兼容旧版 list 格式（非 dict 包装）。"""
        f = tmp_path / "results.json"
        items = [
            {"ticker": "sh.600519", "ta_signal": "BUY", "ranking_score": 0.88},
            {"ticker": "sz.000858", "ta_signal": "OVERWEIGHT", "ranking_score": 0.72},
        ]
        f.write_text(json.dumps(items), encoding="utf-8")
        result = parse_top3(f)
        assert "600519" in result
        assert "000858" in result

    def test_missing_ranking_score_falls_back_to_composite(self, tmp_path: Path) -> None:
        """ranking_score 缺失时回退到 composite_score。"""
        f = tmp_path / "results.json"
        items = [{"ticker": "sh.600519", "ta_signal": "BUY", "composite_score": 0.80}]
        f.write_text(json.dumps({"results": items}), encoding="utf-8")
        result = parse_top3(f)
        assert "600519" in result
        assert "0.8" in result

    def test_missing_signal_uses_question_mark(self, tmp_path: Path) -> None:
        """ta_signal 缺失时使用 '?'。"""
        f = tmp_path / "results.json"
        items = [{"ticker": "sh.600519"}]
        f.write_text(json.dumps({"results": items}), encoding="utf-8")
        result = parse_top3(f)
        assert "600519" in result
        assert "?" in result

    def test_none_input_default_path_not_exists(self, tmp_path: Path) -> None:
        """默认路径不存在时返回空字符串。"""
        # 在临时目录下运行，确保 outputs/results.json 不存在
        import os

        orig_cwd = os.getcwd()
        os.chdir(str(tmp_path))
        try:
            assert parse_top3() == ""
        finally:
            os.chdir(orig_cwd)

    def test_string_type_data_returns_empty(self, tmp_path: Path) -> None:
        """数据为字符串类型时返回空字符串。"""
        f = tmp_path / "results.json"
        f.write_text('"hello"', encoding="utf-8")
        assert parse_top3(f) == ""
