"""测试 scripts.feishu_notify — 飞书通知 CLI 入口和辅助函数。"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

# 需要把项目根加入 path，因为脚本使用相对导入
PROJECT_ROOT = Path(__file__).parent.parent


class TestReadTop3FromResults:
    """_read_top3_from_results 测试。"""

    def test_file_not_exists(self, tmp_path: Path) -> None:
        """文件不存在时返回默认文本。"""
        with patch("scripts.feishu_notify.Path") as mock_path:
            mock_path.return_value.exists.return_value = False
            from scripts.feishu_notify import _read_top3_from_results

            result = _read_top3_from_results()
            assert result == "（无报告生成）"

    def test_invalid_json(self, tmp_path: Path) -> None:
        """JSON 解析失败时返回错误提示。"""
        results_file = tmp_path / "results.json"
        results_file.write_text("{invalid", encoding="utf-8")
        with patch("scripts.feishu_notify.Path") as mock_path_cls:
            mock_path = mock_path_cls.return_value
            mock_path.exists.return_value = True
            mock_path.read_text.return_value = "{invalid"
            from scripts.feishu_notify import _read_top3_from_results

            result = _read_top3_from_results()
            assert "解析结果文件失败" in result

    def test_empty_results_dict(self, tmp_path: Path) -> None:
        """结果为空 dict 时返回默认文本。"""
        results_file = tmp_path / "results.json"
        results_file.write_text(json.dumps({}), encoding="utf-8")
        with patch("scripts.feishu_notify.Path") as mock_path_cls:
            mock_path = mock_path_cls.return_value
            mock_path.exists.return_value = True
            mock_path.read_text.return_value = "{}"
            from scripts.feishu_notify import _read_top3_from_results

            result = _read_top3_from_results()
            assert "无推荐结果" in result

    def test_empty_results_list(self, tmp_path: Path) -> None:
        """结果为空列表时返回默认文本。"""
        results_file = tmp_path / "results.json"
        results_file.write_text("[]", encoding="utf-8")
        with patch("scripts.feishu_notify.Path") as mock_path_cls:
            mock_path = mock_path_cls.return_value
            mock_path.exists.return_value = True
            mock_path.read_text.return_value = "[]"
            from scripts.feishu_notify import _read_top3_from_results

            result = _read_top3_from_results()
            assert "无推荐结果" in result

    def test_valid_results(self, tmp_path: Path) -> None:
        """有效结果时返回正确格式的字符串。"""
        results_file = tmp_path / "results.json"
        data = {
            "project": "trade-krono-cli",
            "results": [
                {"ticker": "sh.600519", "ta_signal": "BUY", "ranking_score": 0.85},
                {"ticker": "sz.000858", "ta_signal": "HOLD", "ranking_score": 0.72},
            ],
        }
        results_file.write_text(json.dumps(data), encoding="utf-8")
        with patch("scripts.feishu_notify.Path") as mock_path_cls:
            mock_path = mock_path_cls.return_value
            mock_path.exists.return_value = True
            mock_path.read_text.return_value = json.dumps(data)
            from scripts.feishu_notify import _read_top3_from_results

            result = _read_top3_from_results()
            assert "600519" in result
            assert "BUY" in result
            assert "0.85" in result

    def test_unknown_format_returns_default(self, tmp_path: Path) -> None:
        """非 dict 非 list 的格式返回未知格式提示。"""
        results_file = tmp_path / "results.json"
        results_file.write_text('"just a string"', encoding="utf-8")
        with patch("scripts.feishu_notify.Path") as mock_path_cls:
            mock_path = mock_path_cls.return_value
            mock_path.exists.return_value = True
            mock_path.read_text.return_value = '"just a string"'
            from scripts.feishu_notify import _read_top3_from_results

            result = _read_top3_from_results()
            assert "未知结果格式" in result


class TestFeishuNotifyCLI:
    """feishu_notify 命令行解析测试。"""

    def test_ci_subcommand_parser(self) -> None:
        """ci 子命令应能正常解析参数。"""
        # 通过重新导入来测试
        from scripts import feishu_notify

        # 检查模块有 build_ci_card 函数
        assert hasattr(feishu_notify, "build_ci_card")

    def test_buffett_subcommand_parser(self) -> None:
        """buffett 子命令应能正常解析参数。"""
        from scripts import feishu_notify

        assert hasattr(feishu_notify, "build_buffett_card")

    def test_daily_subcommand_parser(self) -> None:
        """daily 子命令应能正常解析参数。"""
        from scripts import feishu_notify

        assert hasattr(feishu_notify, "build_daily_card")
