"""fill_missing 选股 — 常量定义。

包含全量历史补拉、今日增量同步所需的常量、路径与阈值。
"""

from __future__ import annotations

from pathlib import Path

# 全量历史拉取起始日期
START_DATE = "2020-01-01"

# 并发线程数
WORKERS = 3

# 单 provider 拉取超时（秒）
FETCH_TIMEOUT = 45

# 缓存数据库路径
CACHE_DB = Path("outputs/cache/pipeline_cache.db")

# 进度文件路径（断点续传用）
PROGRESS_FILE = Path("outputs/results/fill_progress.json")

# 进度文件写入计数器阈值：每 PROGRESS_FLUSH_EVERY 个 ticker 写盘一次
PROGRESS_FLUSH_EVERY = 100

# 判定历史记录不足的最小记录数
FAILED_MIN_RECORDS = 50
