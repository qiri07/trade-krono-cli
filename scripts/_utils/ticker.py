"""scripts._utils.ticker — Ticker 代码归一化与市场判断。

提供 ticker 前缀标准化、北交所/港股/ETF 识别、同花顺 thscode 转换等功能。
各脚本统一引用，避免重复实现一套 ticker 判断逻辑。
"""

from __future__ import annotations

from scripts._utils.constants import (
    BSE_CODE_PREFIXES,
    ETF_SH_PREFIXES,
    ETF_SZ_PREFIXES,
    ST_NAME_KEYWORDS,
    ST_NAME_PREFIXES,
)


def is_bse_code(code: str) -> bool:
    """判断 6 位裸代码是否为北交所股票（920/43/83/87 开头）。"""
    return code.startswith(BSE_CODE_PREFIXES)


def is_bse_or_hk(ticker: str) -> bool:
    """判断带前缀 ticker 或 6 位裸代码是否为需排除的北交所/港股。

    兼容 ``bj.``/``hk.`` 前缀与 6 位裸代码（920 开头北交所）两种形式。
    """
    if ticker.upper().startswith(("BJ.", "HK.")):
        return True
    bare = ticker.split(".")[-1]
    return is_bse_code(bare)


def is_etf_ticker(ticker: str) -> bool:
    """判断 ticker 是否为 ETF 基金。

    沪市 51/52/53/56/58 开头、深市 159/156/158 开头视为 ETF。

    Args:
        ticker: 带交易所前缀的 ticker，如 "sh.510300"、"sz.159915"。

    Returns:
        True 表示该 ticker 是 ETF 基金代码。
    """
    if "." not in ticker:
        return False
    prefix, code = ticker.split(".", 1)
    if prefix == "sh":
        # 沪市 ETF 统一以 5x 开头（51/52/53/55/56/57/58 + 50x 等），一视同仁
        return code.startswith("5") or code[:2] in ETF_SH_PREFIXES
    if prefix == "sz":
        return code[:3] in ETF_SZ_PREFIXES
    return False


def market_key(ticker: str) -> str:
    """把 ticker 归类为市场键（各数据同步脚本统一引用，避免各自维护一套判断）。

    Args:
        ticker: 带交易所前缀的 ticker（如 ``sh.600519``）或 6 位裸代码。

    Returns:
        ``"hk"`` 港股 / ``"bse"`` 北交所 / ``"etf"`` ETF / ``"a"`` A 股。
    """
    upper = ticker.upper()
    if upper.startswith("HK."):
        return "hk"
    if upper.startswith("BJ.") or is_bse_code(ticker.split(".")[-1]):
        return "bse"
    if is_etf_ticker(ticker):
        return "etf"
    return "a"


def normalize_ticker_key(ticker: str) -> str:
    """统一 ticker 键格式：纯数字 → 带交易所前缀。

    metadata.json 中可能同时存在 "sh.600519" 和 "600519" 两种键格式，
    此函数将所有格式归一化为标准前缀格式，避免键不匹配导致数据丢失。

    Args:
        ticker: 任意格式的 ticker，如 "sh.600519"、"sz.000001"、"600519"、"bj.920001"。

    Returns:
        标准格式 ticker，如 "sh.600519"、"sz.000001"、"bj.920001"。
    """
    if "." in ticker:
        return ticker
    code = ticker.strip()
    if not code.isdigit():
        return ticker
    # 港股为 5 位代码（如 00700），不能当深市 6 位代码补零
    if len(code) == 5:
        return f"hk.{code}"
    if code[:2] in ("43", "83", "87") or code.startswith("920"):
        # 北交所（含 920 新码段与 43/83/87 存量码段）
        return f"bj.{code.zfill(6)}"
    if code[:2] in ETF_SH_PREFIXES or code[:3] in ETF_SZ_PREFIXES or code.startswith("5"):
        # ETF 代码：5x 开头（沪市 ETF 全段）或 159/156/158 开头（深市 ETF）
        # 归一化为带交易所前缀格式，使 baostock 能直接识别
        if code.startswith("5") or code[:2] in ETF_SH_PREFIXES:
            # 沪市 ETF（5x 段含 50x/51x/52x/53x/55x/56x/57x/58x）→ sh.<code>
            return f"sh.{code}"
        # 深市 ETF（15x/16x/18x 段）→ sz.<code>
        return f"sz.{code}"
    # 纯数字：根据代码范围推断交易所前缀，保持原始字符串以保留前导零
    # A股代码范围：000xxx~002xxx(深主板) 003xxx~399xxx(深创业板) 600xxx~688xxx(沪主板/科创) 8xxxxx(北交所/新三板) 9xxxxx(北交所)
    num = int(code)
    if num >= 920000:
        return f"bj.{code}"
    elif num >= 830000:
        return f"bj.{code.zfill(6)}"
    elif num >= 600000:
        return f"sh.{code}"
    elif num >= 300000:
        return f"sz.{code}"
    elif num >= 1:
        # 000xxx / 001xxx / 002xxx 深市主板，始终带前导零
        return f"sz.{code.zfill(6)}"
    return ticker


def strip_ticker_prefix(ticker: str) -> str:
    """去除交易所前缀，返回 6 位纯数字代码（``sh.600519`` → ``600519``）。"""
    return ticker.split(".", 1)[1] if "." in ticker else ticker


def ticker_to_thscode(ticker: str) -> str:
    """将 sh.600887 / sz.000001 转换为同花顺 thscode 格式 (600887.SH / 000001.SZ)。"""
    code = ticker.split(".", 1)[1] if "." in ticker else ticker
    if ticker.startswith("sh."):
        return f"{code}.SH"
    elif ticker.startswith("sz."):
        return f"{code}.SZ"
    return code


def is_st_name(name: str, keywords: tuple[str, ...] | None = None) -> bool:
    """判断股票名称是否命中 ST / 异常股规则。

    ``keywords`` 为 None 时使用完整规则：关键词按子串匹配（ST/*ST/退市），
    并对名称首字符做 N/C 前缀匹配（新股/次新）。传入自定义 ``keywords``
    时只做子串匹配，不做前缀匹配。

    Args:
        name: 股票名称（如 ``*ST 金钰``、``N 新股``）。
        keywords: 自定义关键词元组；缺省用完整版 :data:`ST_NAME_KEYWORDS`。

    Returns:
        名称命中任一规则时返回 True。
    """
    kws = ST_NAME_KEYWORDS if keywords is None else keywords
    if any(kw in name for kw in kws):
        return True
    if keywords is not None:
        return False
    head = name.lstrip()
    return any(head.startswith(p) for p in ST_NAME_PREFIXES)
