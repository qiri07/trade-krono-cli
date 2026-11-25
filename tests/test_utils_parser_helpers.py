"""trade_krono_cli.utils.parser_helpers — 向后兼容别名单元测试。

验证 _parse_* 别名正确指向 trade_krono_cli.utils 中的对应函数。
"""

from __future__ import annotations

from trade_krono_cli.utils import (
    merge_with_nested,
    parse_comma_list,
    parse_float,
    parse_range,
)
from trade_krono_cli.utils.parser_helpers import (
    _merge_with_nested,
    _parse_comma_list,
    _parse_float,
    _parse_range,
)


def test_merge_with_nested_alias() -> None:
    assert _merge_with_nested is merge_with_nested


def test_parse_comma_list_alias() -> None:
    assert _parse_comma_list is parse_comma_list


def test_parse_float_alias() -> None:
    assert _parse_float is parse_float


def test_parse_range_alias() -> None:
    assert _parse_range is parse_range


def test_parse_comma_list_basic() -> None:
    assert _parse_comma_list("a,b,c") == ["a", "b", "c"]
    assert _parse_comma_list("") == []
    assert _parse_comma_list("  a , b  ") == ["a", "b"]


def test_parse_float_basic() -> None:
    assert _parse_float("3.14") == 3.14
    assert _parse_float("invalid") is None
    assert _parse_float("") is None


def test_parse_range_basic() -> None:
    assert _parse_range("1,5") == (1.0, 5.0)
    assert _parse_range("") is None
