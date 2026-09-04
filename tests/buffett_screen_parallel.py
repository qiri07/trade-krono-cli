#!/usr/bin/env python3
"""同花顺巴菲特六闸门筛选器 — 并行加速版（curl 子进程，彻底避免连接池卡死）

闸门规则：
  ① 便宜：PE_TTM < 16 且 PB < 3
  ② 好生意：ROE > 15% 且 扣非ROE > 12%
  ③ 财务稳健：资产负债率 < 50%
  ④ 利润是真：经营现金流净额 > 0
  ⑤ 能持续：3年净利CAGR > 0
  ⑥ 安全边际：个股无 PE 历史分位接口，标注为未知
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime

from tests.buffett_cache import (
    cache_clean,
    init_cache,
    load_all_cache,
    save_cache_to_db,
)
from tests.buffett_screening import (
    _CONCURRENCY,
    batch_valuations,
    get_all_stocks,
    run_screening,
    write_result_file,
)


def _notify_feishu(result_file: str, pass_count: int, fail_count: int) -> None:
    """推送巴菲特筛选结果到飞书（失败不中断主流程）。"""
    try:
        result = subprocess.run(
            [
                sys.executable,
                "scripts/feishu_cli.py",
                "buffett",
                "--result-file",
                result_file,
            ],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0:
            print(f"飞书推送成功：{result.stdout.strip()}", flush=True)
        else:
            print(f"飞书推送失败：{result.stderr.strip()}", flush=True)
    except Exception as e:
        print(f"飞书通知异常（不影响结果）：{e}", flush=True)


def main() -> None:
    if (
        not os.getenv("HITHINK_FINANCE_API_KEY", "").strip()
        and not os.getenv("FUYAO_API_KEY", "").strip()
    ):
        print("ERROR: HITHINK_FINANCE_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    # 初始化缓存
    conn = init_cache()
    cleaned = cache_clean(conn)
    if cleaned > 0:
        print(f"缓存清理：清除 {cleaned} 条过期记录", flush=True)

    # 加载所有缓存到内存
    cache = load_all_cache(conn)
    print(
        f"缓存加载：估值 {len(cache.get('valuations', {}))}，财务 {len(cache.get('financials', {}))}",
        flush=True,
    )

    try:
        # 1. 获取全量 A 股列表
        print("步骤 1/4：获取股票列表...", flush=True)
        t0 = time.time()
        stocks = get_all_stocks(conn)
        elapsed = time.time() - t0
        print(f"  获取 {len(stocks)} 只股票（{elapsed:.1f}s）", flush=True)

        if not stocks:
            print("ERROR: 无法获取股票列表", file=sys.stderr)
            sys.exit(1)

        filtered: list[dict] = []
        for s in stocks:
            name = s.get("name", "")
            code = s.get("ticker", "")
            if any(kw in name for kw in ("ST", "*ST", "N", "C")):
                continue
            if code.startswith(("000", "399")):
                continue
            filtered.append(s)
        print(f"  过滤后 {len(filtered)} 只（排除ST/次新）", flush=True)

        # 2. 批量获取估值快照
        print("步骤 2/4：获取估值数据...", flush=True)
        t0 = time.time()
        thscodes = [s["thscode"] for s in filtered]
        vals, val_new_entries = batch_valuations(thscodes, conn, cache)
        cache["valuations"].update(val_new_entries)
        elapsed = time.time() - t0
        hit = sum(1 for k in thscodes if f"val_{k}" in cache.get("valuations", {}))
        miss = len(thscodes) - hit
        print(
            f"  获取 {len(vals)} 只估值（{elapsed:.1f}s，缓存命中 {hit}，请求 {miss}）", flush=True
        )

        # 3. 并行筛选
        print("步骤 3/4：并行筛选...", flush=True)
        results_pass, results_fail, skipped_no_val, elapsed = run_screening(
            filtered, vals, conn, cache, concurrency=_CONCURRENCY
        )

        # 4. 写入结果文件
        date_str = datetime.now().strftime("%Y%m%d")
        out_path = f"outputs/results/buffett_screen_{date_str}.txt"
        write_result_file(out_path, results_pass, results_fail)

        # 5. 保存缓存到数据库
        saved = save_cache_to_db(conn, cache)
        print(f"缓存已保存：{saved} 条新记录", flush=True)

        # 6. 推送飞书通知
        _notify_feishu(out_path, len(results_pass), len(results_fail))

    finally:
        conn.close()


if __name__ == "__main__":
    main()
