"""fill_missing 选股 — 常量定义。

包含全量历史补拉、今日增量同步所需的常量、路径与阈值。
"""

from __future__ import annotations

from pathlib import Path

# 历史数据不足阈值：单一来源为 scripts/_utils.py，此处仅向后兼容再导出。
# 与 scripts/_utils.py 保持一致，禁止在本文件另定义同名常量（两侧取值不同会导致
# check_data_status 与 fill_missing 对「失败股票」判定不一致）。
from scripts._utils import FAILED_MIN_RECORDS as FAILED_MIN_RECORDS

# 全量历史拉取起始日期
START_DATE = "2020-01-01"

# 历史窗口「已覆盖」判定容差：K 线 start 早于此日期即视为已覆盖完整回补窗口。
# START_DATE=2020-01-01，A 股 2020 年首个交易日为 01-02，此处留出数日容差。
FULL_HISTORY_START = "2020-01-10"

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

# 历史数据连续性阈值：K 线根数 / 估算交易日数 ≥ 该值视为数据完整。
# 用于兜底判定次新股 / 次新 ETF（上市仅数月，根数天然少于 FAILED_MIN_RECORDS），
# 同时把抓取失败留下的、中间有大量空洞的稀疏片段判为不足。
HISTORY_DENSITY_MIN = 0.7

# 今日增量补拉的回看窗口（自然日）。
# 以「应已落库的最近交易日」为终点向前回看该天数，可顺带补齐中间缺失的交易日
# （如港股数据源滞后数日时，单日窗口只能补到最新一天，留下中间空洞）。
# 合并时按 timestamps 去重（keep="last"），重复拉取已有交易日是幂等的。
INCREMENTAL_LOOKBACK_DAYS = 10
