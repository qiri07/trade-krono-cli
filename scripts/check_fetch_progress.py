#!/usr/bin/env python3
"""定时检查 fetch_reference_data.py 进度。"""
from __future__ import annotations

import re
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

LOG_FILE = Path("/tmp/fetch_ref_final.log")
METADATA_FILE = Path("outputs/cache/metadata.json")
MONITOR_LOG = Path("/tmp/fetch_monitor.log")


def check_process() -> int:
    """返回运行中的 fetch_reference 进程数。"""
    result = subprocess.run(
        ["ps", "aux"], capture_output=True, text=True
    )
    return len(
        [
            line
            for line in result.stdout.split("\n")
            if "fetch_reference_data.py" in line and "grep" not in line
        ]
    )


def parse_batches() -> list[tuple[int, str]]:
    """解析日志中的批次信息。"""
    batches: list[tuple[int, str]] = []
    if not LOG_FILE.exists():
        return batches
    content = LOG_FILE.read_text(encoding="utf-8")
    for line in content.split("\n"):
        m = re.search(r"批次 (\d+) \[\d+~\d+/5575\]", line)
        ts_m = re.search(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", line)
        if m and ts_m:
            batches.append((int(m.group(1)), ts_m.group(1)))
    return batches


def get_metadata_count() -> int:
    """获取 metadata.json 条目数。"""
    if not METADATA_FILE.exists():
        return 0
    try:
        import json

        d = json.load(open(METADATA_FILE, encoding="utf-8"))
        return len(d)
    except Exception:
        return 0


def estimate_completion(batches: list[tuple[int, str]]) -> str:
    """估算完成时间。"""
    if len(batches) < 2:
        return "数据不足"
    t1 = datetime.strptime(batches[0][1], "%Y-%m-%d %H:%M:%S")
    t2 = datetime.strptime(batches[-1][1], "%Y-%m-%d %H:%M:%S")
    elapsed = (t2 - t1).total_seconds()
    batch_count = batches[-1][0] - batches[0][0] + 1
    avg_per_batch = elapsed / batch_count / 60  # minutes
    remaining = 59 - batches[-1][0]
    eta_minutes = remaining * avg_per_batch
    eta = datetime.now() + timedelta(minutes=eta_minutes)
    return f"{eta.strftime('%Y-%m-%d %H:%M')} (约 {eta_minutes/60:.1f} 小时后)"


def main() -> None:
    running = check_process()
    batches = parse_batches()
    meta_count = get_metadata_count()

    # 构建输出
    lines = [f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}]"]

    if running == 0:
        if meta_count > 5:
            lines.append(f"✅ 已完成! metadata: {meta_count} entries")
        else:
            lines.append(f"❌ 已退出但 metadata 仍为 {meta_count} 条（可能失败）")
    else:
        if batches:
            current = batches[-1][0]
            pct = current * 100 // 59
            lines.append(f"🔄 运行中 ({current}/59 批次, {pct}%)")
            lines.append(f"   预计完成: {estimate_completion(batches)}")
        else:
            lines.append("🔄 运行中 (无批次记录)")

    lines.append(f"   metadata.json: {meta_count} entries")

    output = "\n".join(lines)
    print(output)

    # 追加到监控日志
    with MONITOR_LOG.open("a", encoding="utf-8") as f:
        f.write(output + "\n")


if __name__ == "__main__":
    main()
