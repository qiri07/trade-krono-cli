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
    """将逗号分隔的股票代码转为带交易所前缀的 ticker 列表。

    规则：
    - 已带前缀（如 "hk.00700"、"sh.600519"、"us.BABA"）：原样保留。
    - 6 位纯数字 A 股：6xxxxx→sh.，0/3xxxxx→sz.，9xxxxx→bj.
    - 6 位纯数字 ETF（51/58/159/156/158 开头）：归一化为 "etf.<code>"。
    - 期货合约（字母数字混合，如 IC2610）：原样保留，由上层 provider 层路由。
    - 非法字符或超过 8 位的代码：跳过并告警。
    自动去重并保持首次出现的顺序。
    """
    seen: set[str] = set()
    result: list[str] = []
    if not raw:
        return result
    for code in (c.strip() for c in raw.split(",") if c.strip()):
        if "." in code:
            # 已带交易所前缀（hk. / us. / etf. 等），原样保留
            if code not in seen:
                seen.add(code)
                result.append(code)
            continue
        if not code.isalnum() or len(code) > 8:
            # 非法字符或过长代码：跳过
            logger.warning(f"⚠️  白名单代码格式错误，已跳过: {code}")
            continue
        if code.isdigit() and len(code) != 6:
            # 长度错误的纯数字：跳过（既不是 A 股也不是期货）
            logger.warning(f"⚠️  白名单代码格式错误，已跳过: {code}")
            continue
        if code.isdigit() and (code[:2] in ("51", "58") or code[:3] in ("159", "156", "158")):
            # ETF 代码：51xxxx/588xxx(沪)、159xxx/156xxx(深) → "etf.<code>"
            ticker = f"etf.{code}"
        elif code.isdigit():
            prefix = _EXCHANGE_PREFIX.get(code[0])
            if prefix is None:
                logger.warning(f"⚠️  未知交易所前缀，已跳过: {code}")
                continue
            ticker = f"{prefix}{code}"
        else:
            # 期货合约（IC2610/IH2610/IF2610）等字母数字混合代码，原样保留
            ticker = code
        if ticker not in seen:
            seen.add(ticker)
            result.append(ticker)
    return result
