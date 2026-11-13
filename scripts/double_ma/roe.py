"""double_ma 选股 — ROE 相关。

负责从同花顺 API 获取 ROE，以及从巴菲特缓存数据库加载 ROE 和净利润数据。
"""

from __future__ import annotations

import json
from pathlib import Path

import requests

from scripts._utils import get_sqlite_conn
from scripts.double_ma.constants import API_KEY, BASE, HEADERS


def fetch_roe_from_api(thscode: str) -> float | None:
    """从同花顺 Fuyao API 获取最新 ROE。

    Parameters
    ----------
    thscode : str
        同花顺标准代码（形如 600519.SH）。

    Returns
    -------
    float | None
        ROE 值（百分比），获取失败时返回 None。
    """
    if not API_KEY:
        return None
    try:
        resp = requests.post(
            f"{BASE}/api/a-share/financials/indicators",
            headers=HEADERS,
            json={"thscode": thscode, "report": "latest"},
            timeout=8,
        )
        data = resp.json()
        if data.get("code") != 0:
            return None
        for ab in data.get("data", {}).get("abilities", []):
            for ind in ab.get("indicators", []):
                if ind.get("index_id") == "index_weighted_avg_roe":
                    try:
                        return float(ind.get("value"))
                    except (ValueError, TypeError):
                        continue
        return None
    except Exception:  # noqa: BLE001
        return None


def load_roe_from_cache(buffett_cache_path: Path) -> dict[str, float]:
    """从巴菲特缓存数据库加载 ROE 数据。

    Parameters
    ----------
    buffett_cache_path : Path
        ``buffett_cache.db`` 路径。

    Returns
    -------
    dict[str, float]
        thscode → ROE 值的映射（取多个期间中的最大值）。
    """
    if not buffett_cache_path.exists():
        return {}
    conn = get_sqlite_conn(buffett_cache_path)
    rows = conn.execute("SELECT key, data FROM cache_financials").fetchall()
    conn.close()

    roe_map: dict[str, float] = {}
    for key, data_str in rows:
        if not key.startswith("fin_"):
            continue
        parts = key.split("_")
        if len(parts) < 2:
            continue
        thscode = parts[1]
        try:
            d = json.loads(data_str)
            roe = d.get("roe")
            if roe is not None and roe > 0:
                if thscode not in roe_map or roe > roe_map[thscode]:
                    roe_map[thscode] = roe
        except (json.JSONDecodeError, TypeError):
            continue
    return roe_map


def load_profit_from_cache(buffett_cache_path: Path) -> dict[str, float]:
    """从巴菲特缓存数据库加载最新净利润（parent_holder_net_profit，单位：元）。

    Parameters
    ----------
    buffett_cache_path : Path
        ``buffett_cache.db`` 路径。

    Returns
    -------
    dict[str, float]
        thscode → 净利润（元）的映射。
    """
    if not buffett_cache_path.exists():
        return {}
    conn = get_sqlite_conn(buffett_cache_path)
    rows = conn.execute("SELECT key, data FROM cache_income").fetchall()
    conn.close()

    profit_map: dict[str, float] = {}
    for key, data_str in rows:
        if not key.startswith("income_"):
            continue
        parts = key.split("_", 1)
        if len(parts) < 2:
            continue
        thscode = parts[1]  # e.g. "601838.SH"
        try:
            d = json.loads(data_str)
            items = d.get("items", [])
            if not items:
                continue
            # 按 fiscal_year 降序取最新
            items.sort(key=lambda x: x.get("fiscal_year", 0), reverse=True)
            net_profit = items[0].get("parent_holder_net_profit")
            if net_profit is not None:
                try:
                    profit_map[thscode] = float(net_profit)
                except (ValueError, TypeError):
                    pass
        except (json.JSONDecodeError, TypeError):
            continue
    return profit_map
