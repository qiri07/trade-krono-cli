"""两阶段筛选：双均线金叉+PE+MACD -> 再过滤ROE>15%"""
from __future__ import annotations

import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

import requests
from loguru import logger

sys.path.insert(0, "/run/media/onai/MyDisk/Work/trade-krono-cli")

from scripts._utils import ticker_to_thscode

API_KEY = (
    os.getenv("HITHINK_FINANCE_API_KEY", "")
    or os.getenv("FUYAO_API_KEY", "")
).strip()
if not API_KEY:
    logger.error("缺少 HITHINK_FINANCE_API_KEY 或 FUYAO_API_KEY")
    sys.exit(1)

HEADERS = {"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"}
BASE = "https://fuyao.aicubes.cn"




def fetch_roe(thscode: str) -> float | None:
    """获取最新报告期ROE（加权平均）"""
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
                        pass
    except Exception as e:
        logger.debug(f"{thscode} ROE获取失败: {e}")
    return None


def main() -> None:
    date_tag = datetime.now().strftime("%Y-%m-%d")
    output_dir = Path("outputs/results")
    output_dir.mkdir(parents=True, exist_ok=True)

    # Phase 1: 读取已有筛选结果（MA+PE+MACD）
    prev_file = output_dir / f"double_ma_golden_cross_pe_macd_{date_tag}.json"
    if not prev_file.exists():
        logger.error(f"找不到前置筛选结果: {prev_file}")
        sys.exit(1)

    candidates = json.load(open(prev_file, encoding="utf-8"))
    logger.info(f"Phase 1 候选: {len(candidates)} 只，进入 ROE 过滤...")

    # Phase 2: 并行获取 ROE
    results: list[dict] = []
    skipped_no_roe = 0
    skipped_roe_low = 0
    skipped_error = 0

    with ThreadPoolExecutor(max_workers=20) as exe:
        futures = {
            exe.submit(fetch_roe, ticker_to_thscode(s["ticker"])): s
            for s in candidates
        }
        for i, future in enumerate(as_completed(futures), 1):
            s = futures[future]
            try:
                roe = future.result()
            except Exception as e:
                logger.debug(f"{s['ticker']} ROE异常: {e}")
                skipped_error += 1
                continue

            if roe is None:
                skipped_no_roe += 1
                continue
            if roe <= 15:
                skipped_roe_low += 1
                continue

            entry = {**s, "roe": round(roe, 2)}
            results.append(entry)

            if i % 50 == 0:
                logger.info(f"ROE获取进度: {i}/{len(candidates)}")

    # 按PE升序排列
    results.sort(key=lambda x: x["pe_ttm"])

    logger.info("=" * 60)
    logger.info("筛选完成！")
    logger.info(f"  候选（MA+PE+MACD）: {len(candidates)} 只")
    logger.info(f"  通过ROE>15%: {len(results)} 只")
    logger.info(f"  - ROE缺失: {skipped_no_roe}")
    logger.info(f"  - ROE<=15%: {skipped_roe_low}")
    logger.info(f"  - 获取异常: {skipped_error}")
    logger.info("=" * 60)

    out_file = output_dir / f"double_ma_golden_cross_pe_macd_roe_{date_tag}.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    logger.info(f"结果已保存: {out_file}")

    logger.info(f"\nTOP {min(30, len(results))} 股票:")
    logger.info(
        f"{'序号':<4} {'代码':<10} {'名称':<10} {'行业':<10} "
        f"{'收盘':<8} {'PE':<6} {'ROE%':<6} {'日Ratio':<8} {'周Ratio':<8} {'MACD':<10}"
    )
    logger.info("-" * 90)
    for i, s in enumerate(results[:30], 1):
        logger.info(
            f"{i:<4} {s['ticker']:<10} {s['name'] or 'N/A':<10} {s['industry'] or 'N/A':<10} "
            f"{s['latest_close']:<8.2f} {s['pe_ttm']:<6.2f} {s['roe']:<6.2f} "
            f"{s['daily_ratio']:<8.4f} {s['weekly_ratio']:<8.4f} {s['macd']:<10.6f}"
        )

    print(f"RESULT_FILE={out_file}")
    print(f"COUNT={len(results)}")


if __name__ == "__main__":
    main()
