"""double_ma 选股 — PE 相关。

负责从同花顺 API 批量获取 PE/名称/行业，以及就地补充缺失 PE。
"""

from __future__ import annotations

import requests
from loguru import logger

from scripts._utils import ticker_to_thscode
from scripts.double_ma.constants import API_KEY, BASE


def _fetch_pe_batch_api(thscodes: list[str]) -> dict[str, dict]:
    """从同花顺 Fuyao API 批量获取 PE/名称/行业（单批最多约 100 只）。

    请求使用 X-api-key 头（快照接口不使用 Bearer 认证）。返回
    {六位代码: {"pe_ttm", "name", "industry"}} 映射；无 API_KEY 或
    请求失败时返回空字典（调用方自行降级处理）。

    Parameters
    ----------
    thscodes : list[str]
        同花顺标准代码列表（形如 600519.SH / 000858.SZ）
    """
    if not API_KEY or not thscodes:
        return {}
    pe_map: dict[str, dict] = {}
    for i in range(0, len(thscodes), 100):
        batch = ",".join(thscodes[i : i + 100])
        try:
            resp = requests.get(
                f"{BASE}/api/a-share/valuations/snapshot",
                params={"thscodes": batch},
                headers={"X-api-key": API_KEY},
                timeout=30,
            )
            data = resp.json()
            if data.get("code") != 0:
                continue
            for item in data.get("data", {}).get("item", []):
                ths = item.get("thscode", "")
                code6 = ths.split(".")[0] if "." in ths else ths
                try:
                    pe_val = float(item.get("pe_ttm"))
                except (ValueError, TypeError):
                    pe_val = None
                if pe_val is not None:
                    pe_map[code6] = {
                        "pe_ttm": pe_val,
                        "name": (item.get("name") or "").strip(),
                        "industry": item.get("industry", ""),
                    }
        except Exception as e:
            logger.warning(f"  Fuyao API 批次 {i // 100 + 1} 失败: {str(e)[:80]}")
    return pe_map


def _backfill_pe_from_api(results: list[dict]) -> int:
    """就地补充列表中 pe_ttm 缺失股票的 PE/名称/行业（Fuyao API）。

    仅处理 pe_ttm 为 None 的条目；已有本地 PE 的条目不动。

    Parameters
    ----------
    results : list[dict]
        股票字典列表（需含 ticker / name / industry / pe_ttm 键）

    Returns
    -------
    int
        成功补充 PE 的股票数量
    """
    missing = [s for s in results if s.get("pe_ttm") is None]
    if not missing:
        return 0
    if not API_KEY:
        logger.warning(
            f"  PE 回退: Fuyao API_KEY 未配置，{len(missing)} 只股票 PE 保持缺失"
        )
        return 0

    logger.info(f"  PE 回退: 从 Fuyao API 补充 {len(missing)} 只股票的 PE/行业...")
    thscodes = [ticker_to_thscode(s["ticker"]) for s in missing]
    pe_map = _fetch_pe_batch_api(thscodes)

    filled = 0
    for s in missing:
        code6 = s["ticker"].split(".")[1]
        info = pe_map.get(code6)
        if info and info.get("pe_ttm") is not None:
            s["pe_ttm"] = info["pe_ttm"]
            if not s.get("name"):
                s["name"] = info.get("name", "")
            if s.get("industry") in (None, "", "N/A"):
                s["industry"] = info.get("industry") or "N/A"
            filled += 1
    logger.info(
        f"  PE 回退完成: 补充 {filled}/{len(missing)} 只，剩余缺失 {len(missing) - filled} 只"
    )
    return filled


def _fill_pe_and_industry(results: list[dict]) -> None:
    """两阶段：先补名称/行业（全量批次），再对 PE 仍缺失的补 PE。

    本地 metadata 过期（条目极少）时，名称/行业全空，PE 也无值。
    先用快照接口全量补充名称/行业，再对 PE 仍缺失的补充 PE，
    避免把 700+ 只全部塞进单个 API 请求。

    Parameters
    ----------
    results : list[dict]
        技术面通过的股票列表（就地修改 name / industry / pe_ttm）
    """
    need_name = [s for s in results if not s.get("name") or s.get("industry") in (None, "")]
    if need_name:
        logger.info(f"  名称/行业缺失 {len(need_name)} 只，先从 Fuyao API 补充...")
        thscodes = [ticker_to_thscode(s["ticker"]) for s in need_name]
        info_map = _fetch_pe_batch_api(thscodes)
        for s in need_name:
            info = info_map.get(s["ticker"].split(".")[1])
            if not info:
                continue
            if not s.get("name"):
                s["name"] = info.get("name", "")
            if s.get("industry") in (None, "", "N/A"):
                s["industry"] = info.get("industry") or "N/A"
    _backfill_pe_from_api(results)
