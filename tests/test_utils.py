"""测试 trade_krono_cli.utils — 共享工具函数（补充 test_logger_globals_utils.py 未覆盖的部分）。"""

from __future__ import annotations

from dataclasses import dataclass, field

from trade_krono_cli.utils import merge_with_nested, parse_comma_list, parse_float, parse_range


class TestParseRange:
    """parse_range 测试。"""

    def test_valid_range(self) -> None:
        assert parse_range("1.0, 2.0") == (1.0, 2.0)

    def test_whitespace_stripped(self) -> None:
        assert parse_range("  5.5  ,  10.0  ") == (5.5, 10.0)

    def test_empty_string_returns_none(self) -> None:
        assert parse_range("") is None

    def test_none_input_returns_none(self) -> None:
        assert parse_range(None) is None  # type: ignore[arg-type]

    def test_single_value_returns_none(self) -> None:
        assert parse_range("5.0") is None

    def test_three_values_returns_none(self) -> None:
        assert parse_range("1.0, 2.0, 3.0") is None

    def test_invalid_float_returns_none(self) -> None:
        assert parse_range("abc, def") is None

    def test_integer_values(self) -> None:
        assert parse_range("1, 10") == (1.0, 10.0)


class TestParseCommaList:
    """parse_comma_list 测试。"""

    def test_basic_list(self) -> None:
        assert parse_comma_list("a, b, c") == ["a", "b", "c"]

    def test_empty_string_returns_empty(self) -> None:
        assert parse_comma_list("") == []

    def test_none_input_returns_empty(self) -> None:
        assert parse_comma_list(None) == []  # type: ignore[arg-type]

    def test_whitespace_stripped(self) -> None:
        assert parse_comma_list(" a , b , c ") == ["a", "b", "c"]

    def test_empty_entries_skipped(self) -> None:
        assert parse_comma_list("a,,b,,,c") == ["a", "b", "c"]

    def test_single_item(self) -> None:
        assert parse_comma_list("only_one") == ["only_one"]


class TestParseFloat:
    """parse_float 测试。"""

    def test_valid_float(self) -> None:
        assert parse_float("3.14") == 3.14

    def test_integer_string(self) -> None:
        assert parse_float("42") == 42.0

    def test_whitespace_stripped(self) -> None:
        assert parse_float("  1.5  ") == 1.5

    def test_empty_string_returns_none(self) -> None:
        assert parse_float("") is None

    def test_none_input_returns_none(self) -> None:
        assert parse_float(None) is None  # type: ignore[arg-type]

    def test_invalid_string_returns_none(self) -> None:
        assert parse_float("abc") is None

    def test_negative_float(self) -> None:
        assert parse_float("-2.5") == -2.5


class TestMergeWithNested:
    """merge_with_nested 测试。"""

    def test_no_merge_method_returns_obj_unchanged(self) -> None:
        obj = {"a": 1}
        result = merge_with_nested(obj, {"b": 2})
        assert result == {"a": 1}

    def test_flat_override(self) -> None:
        """平铺覆盖。"""
        from dataclasses import dataclass

        @dataclass
        class Cfg:
            x: int = 1
            y: str = "a"

            def merge(self, **kwargs):
                return Cfg(**{**{"x": self.x, "y": self.y}, **kwargs})

        cfg = Cfg()
        merged = merge_with_nested(cfg, {"x": 10})
        assert merged.x == 10
        assert merged.y == "a"

    def test_nested_override_with_double_underscore(self) -> None:
        """嵌套路径覆盖（__ 分隔）。"""
        from typing import Any

        @dataclass
        class Inner:
            vol: float = 0.5

            def merge(self, **kwargs: Any) -> "Inner":
                if isinstance(kwargs.get("vol"), dict):
                    return Inner(**kwargs["vol"])
                return Inner(**{**{"vol": self.vol}, **kwargs})

        @dataclass
        class Outer:
            risk: Inner = field(default_factory=Inner)
            label: str = "test"

            def merge(self, **kwargs: Any) -> "Outer":
                if isinstance(kwargs.get("risk"), dict):
                    return Outer(
                        risk=Inner(**kwargs.pop("risk")), label=kwargs.get("label", self.label)
                    )
                return Outer(**{**{"risk": self.risk, "label": self.label}, **kwargs})

        cfg = Outer()
        merged = merge_with_nested(cfg, {"risk__vol": 0.8})
        assert merged.risk.vol == 0.8

    def test_multiple_nested_keys(self) -> None:
        """多个嵌套路径同时覆盖。"""
        from typing import Any

        @dataclass
        class Inner:
            vol: float = 0.5
            drawdown: float = 0.3

            def merge(self, **kwargs: Any) -> "Inner":
                if isinstance(kwargs.get("vol"), dict):
                    return Inner(**kwargs["vol"])
                return Inner(**{**{"vol": self.vol, "drawdown": self.drawdown}, **kwargs})

        @dataclass
        class Outer:
            risk: Inner = field(default_factory=Inner)
            label: str = "test"

            def merge(self, **kwargs: Any) -> "Outer":
                if isinstance(kwargs.get("risk"), dict):
                    return Outer(
                        risk=Inner(**kwargs.pop("risk")), label=kwargs.get("label", self.label)
                    )
                return Outer(**{**{"risk": self.risk, "label": self.label}, **kwargs})

        cfg = Outer()
        merged = merge_with_nested(
            cfg, {"risk__vol": 0.9, "risk__drawdown": 0.4, "label": "updated"}
        )
        assert merged.risk.vol == 0.9
        assert merged.risk.drawdown == 0.4
        assert merged.label == "updated"
