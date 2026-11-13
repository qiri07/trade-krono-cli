"""six_dim 选股 — 总市值维度。

负责从东财实时快照获取总市值，用于市值过滤门槛。
"""

from __future__ import annotations

from pathlib import Path

import akshare as ak
from loguru import logger


def fetch_spot_snapshot(
    cache_dir: Path, date_str: str
) -> dict[str, dict[str, float]]:
    """获取东财全市场快照（总市值/流通市值/量比/换手率），按日缓存。

    Args:
        cache_dir: 缓存目录（``outputs/cache``）。
        date_str: 日期字符串（YYYY-MM-DD），用于缓存文件名与 TTL。

    Returns:
        ``code -> {"market_cap_yi": 总市值(亿), "float_cap_yi": 流通市值(亿), ...}``。
    """
    cache_file = cache_dir / f"spot_snapshot_{date_str.replace('-', '')}.json"
    if cache_file.exists():
        import json

        try:
            return json.loads(cache_file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass

    try:
        df = ak.stock_zh_a_spot_em()
        if df is None or df.empty:
            logger.warning("东财实时快照为空，市值维度将跳过市值过滤")
            return {}
    except Exception as exc:  # noqa: BLE001 - akshare 网络异常不可控
        logger.warning(f"东财实时快照获取失败：{exc}")
        return {}

    out: dict[str, dict[str, float]] = {}
    for _, row in df.iterrows():
        code = str(row.get("代码", "")).zfill(6)
        try:
            out[code] = {
                "market_cap_yi": float(row.get("总市值") or 0),
                "float_cap_yi": float(row.get("流通市值") or 0),
                "volume_ratio": float(row.get("量比") or 0),
                "turnover": float(row.get("换手率") or 0),
            }
        except (TypeError, ValueError):
            continue

    try:
        import json

        cache_file.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass

    logger.info(f"总市值快照：{len(out)} 只")
    return out
