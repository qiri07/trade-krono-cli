"""Parser helpers — re-exported from trade_krono_cli.utils for backward compatibility."""

from trade_krono_cli.utils import (
    merge_with_nested,
    parse_comma_list,
    parse_float,
    parse_range,
)

# Legacy private names kept for callers that used the old _parse_* naming.
_parse_range = parse_range
_parse_comma_list = parse_comma_list
_parse_float = parse_float
_merge_with_nested = merge_with_nested

__all__ = [
    "parse_range",
    "parse_comma_list",
    "parse_float",
    "merge_with_nested",
    "_parse_range",
    "_parse_comma_list",
    "_parse_float",
    "_merge_with_nested",
]
