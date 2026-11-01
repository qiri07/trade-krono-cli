# cache_sync.sh — A 股数据缓存检查与同步 Cron 包装

## 作用

Cron 定时任务包装脚本。在**交易日 10:00 和 15:30** 各执行一次数据缓存检查与同步（开盘前 + 收盘后），保证 K 线缓存保持最新。

实际检查/同步逻辑在 `scripts/check_and_sync_cache.py`，本脚本只负责：
- 设置 cron 环境（`PATH`、`PYTHONIOENCODING`）
- 定位项目根目录
- 调用 `uv run python scripts/check_and_sync_cache.py`
- 记录开始/结束时间与退出码到日志

> 注：`check_and_sync_cache.py` 是 cron 专用入口（与 `scripts/check_data_status.py` 的检查逻辑互补，前者偏「自动补同步」，后者偏「产出补拉计划 JSON」）。

## 用法

```bash
# 手动执行（等价于 cron 触发）
bash scripts/cache_sync.sh
```

## 安装 cron 任务

```bash
crontab -e
# 添加（交易日 10:00 和 15:30）：
0 10,30 15 * * 1-5 /path/to/trade-krono-cli/scripts/cache_sync.sh
```

> 说明：`10:00` 为开盘前预热缓存，`15:30` 为收盘后补齐当日增量。`1-5` 表示周一到周五。

## 日志

每次执行写入当日日志（同日多次运行追加到同一文件）：

```
outputs/logs/cache_sync_<YYYYMMDD>.log
```

## 退出码

- `0`：检查/同步成功
- 非 `0`：失败（透传 Python 进程退出码）

## 注意事项

- 使用 `set -euo pipefail`，任一步失败立即退出。
- cron 非交互环境，脚本内显式 `export PATH` 确保能找到 `uv`。
