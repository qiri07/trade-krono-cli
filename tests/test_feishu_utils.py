"""测试 scripts.feishu_utils — 飞书卡片构建和发送工具函数。"""

from __future__ import annotations

from scripts.feishu_utils import (
    _now_cn,
    _status_emoji,
    _template,
    build_buffett_card,
    build_ci_card,
    build_daily_card,
)


class TestHelpers:
    """私有辅助函数测试。"""

    def test_template_success(self) -> None:
        assert _template("success") == "green"

    def test_template_failure(self) -> None:
        assert _template("failure") == "red"

    def test_template_cancelled(self) -> None:
        assert _template("cancelled") == "grey"

    def test_template_unknown_returns_blue(self) -> None:
        assert _template("unknown") == "blue"

    def test_status_emoji_success(self) -> None:
        assert _status_emoji("success") == "✅"

    def test_status_emoji_failure(self) -> None:
        assert _status_emoji("failure") == "❌"

    def test_status_emoji_cancelled(self) -> None:
        assert _status_emoji("cancelled") == "⏹️"

    def test_status_emoji_unknown_returns_circle(self) -> None:
        assert _status_emoji("unknown") == "⚪"

    def test_now_cn_returns_string(self) -> None:
        result = _now_cn()
        assert isinstance(result, str)
        assert len(result) > 0
        # 格式应类似 "2026-09-08 12:00:00"
        assert "-" in result and ":" in result


class TestBuildCiCard:
    """build_ci_card 测试。"""

    def test_success_card_structure(self) -> None:
        card = build_ci_card("success", "main", "abc12345", "lint✅ test✅", "https://ci.run/1")
        assert card["msg_type"] == "interactive"
        assert "card" in card
        header = card["card"]["header"]
        assert "🔧" in header["title"]["content"]
        assert "✅" in header["title"]["content"]
        assert header["template"] == "green"
        elements = card["card"]["elements"]
        assert len(elements) == 1
        text_content = elements[0]["text"]["content"]
        assert "trade-krono-cli" in text_content
        assert "main" in text_content
        assert "abc1234" in text_content  # 前8位
        assert "https://ci.run/1" in text_content

    def test_failure_card(self) -> None:
        card = build_ci_card("failure", "dev", "deadbeef", "test❌", "https://ci.run/2")
        assert card["card"]["header"]["template"] == "red"
        assert "❌" in card["card"]["header"]["title"]["content"]

    def test_cancelled_card(self) -> None:
        card = build_ci_card("cancelled", "feat/x", "12345678", "lint⏹️", "https://ci.run/3")
        assert card["card"]["header"]["template"] == "grey"
        assert "⏹️" in card["card"]["header"]["title"]["content"]


class TestBuildDailyCard:
    """build_daily_card 测试。"""

    def test_card_structure(self) -> None:
        card = build_daily_card(
            "success", "2026-09-08", "600519,000858", "600519:BUY 0.85", "https://run/1"
        )
        assert card["msg_type"] == "interactive"
        assert "card" in card
        header = card["card"]["header"]
        assert "📊" in header["title"]["content"]
        assert header["template"] == "green"
        text = card["card"]["elements"][0]["text"]["content"]
        assert "2026-09-08" in text
        assert "600519,000858" in text
        assert "600519:BUY 0.85" in text

    def test_failure_daily_card(self) -> None:
        card = build_daily_card("failure", "2026-09-08", "600519", "N/A", "")
        assert card["card"]["header"]["template"] == "red"


class TestBuildBuffettCard:
    """build_buffett_card 测试。"""

    def test_card_structure(self, tmp_path) -> None:
        result_file = tmp_path / "buffett.txt"
        result_file.write_text(
            "巴菲特六闸门筛选结果\n"
            "通过五闸门: 共 5 只\n"
            "--------\n"
            "600519 贵州茅台 18.5 3.2 25.1\n"
            "000858 五粮液 22.0 4.1 20.3\n",
            encoding="utf-8",
        )
        card = build_buffett_card(str(result_file))
        assert card["msg_type"] == "interactive"
        header = card["card"]["header"]
        assert "📈" in header["title"]["content"]
        elements = card["card"]["elements"]
        assert len(elements) >= 1
        text_content = elements[0]["text"]["content"]
        assert "2026-09-08" in text_content

    def test_failure_card(self, tmp_path) -> None:
        result_file = tmp_path / "buffett_fail.txt"
        result_file.write_text(
            "筛选失败：API超时\n共 0 只\n",
            encoding="utf-8",
        )
        card = build_buffett_card(str(result_file))
        # pass_count=0 → template=red
        assert card["card"]["header"]["template"] == "red"

    def test_missing_result_file_returns_text_card(self, tmp_path) -> None:
        card = build_buffett_card(str(tmp_path / "missing.txt"))
        assert card["msg_type"] == "text"
        assert "不存在" in card["text"]


class TestBuildTextCard:
    """build_text_card 测试（在 feishu_core 中定义）。"""

    def test_card_structure_in_feishu_core(self) -> None:
        """验证 feishu_core 有 build_text_card。"""
        from scripts.feishu_core import build_text_card

        card = build_text_card("这是一条测试消息")
        assert card["msg_type"] == "interactive"
        elements = card["card"]["elements"]
        assert len(elements) == 1
        assert elements[0]["tag"] == "div"
        assert "这是一条测试消息" in elements[0]["text"]["content"]
