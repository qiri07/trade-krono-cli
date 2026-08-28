"""
Parser helpers — thin backward-compatibility aliases for _parse_* callers.

Public functions live in trade_krono_cli.utils; this module exists so that
callers which previously imported _parse_* from pipeline_config or
pipeline.config_loader continue to work without change.
"""

from __future__ import annotations

from trade_krono_cli.utils import (
    merge_with_nested as _merge_with_nested,
)
from trade_krono_cli.utils import (
    parse_comma_list as _parse_comma_list,
)
from trade_krono_cli.utils import (
    parse_float as _parse_float,
)
from trade_krono_cli.utils import (
    parse_range as _parse_range,
)

__all__ = [
    "_parse_range",
    "_parse_comma_list",
    "_parse_float",
    "_merge_with_nested",
]
