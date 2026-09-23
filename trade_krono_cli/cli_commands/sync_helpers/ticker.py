"""sync_helpers.ticker — 股票代码解析。

将逗号分隔的6位股票代码转为带交易所前缀的 ticker 列表。
"""

from __future__ import annotations

from loguru import logger

_EXCHANGE_PREFIX: dict[str, str] = {
    "6": "sh.",  # 上交所主板 + 科创板
    "0": "sz.",  # 深交所主板
    "3": "sz.",  # 创业板
    "9": "bj.",  # 北交所
}


def _resolve_tickers(raw: str) -> list[str]:
    """将逗号分隔的6位股票代码转为带交易所前缀的 ticker 列表。

    规则：6xxxxx→sh.，0/3xxxxx→sz.，9xxxxx→bj.。
    自动去重并保持首次出现的顺序。
    """
    seen: set[str] = set()
    result: list[str] = []
    if not raw:
        return result
    for code in (c.strip() for c in raw.split(",") if c.strip()):
        if len(code) != 6 or not code.isdigit():
            logger.warning(f"⚠️  白名单代码格式错误，已跳过: {code}")
            continue
        prefix = _EXCHANGE_PREFIX.get(code[0])
        if prefix is None:
            logger.warning(f"⚠️  未知交易所前缀，已跳过: {code}")
            continue
        ticker = f"{prefix}{code}"
        if ticker not in seen:
            seen.add(ticker)
            result.append(ticker)
    return result
