# buffett_screen_cron.sh — 巴菲特六闸门筛选 Cron 包装

## 作用

Cron 定时任务包装脚本。在每个**交易日 16:10**（收盘后数据完整）自动执行巴菲特六闸门全量筛选（并行版），并把日志落盘到 `outputs/logs/`。

实际筛选逻辑在 `tests/buffett_screen_parallel.py`，本脚本只负责：
- 设置 cron 环境（`PATH`、`PYTHONIOENCODING`）
- 定位项目根目录
- 调用 `uv run python tests/buffett_screen_parallel.py`
- 记录开始/结束时间与退出码到日志

## 用法

```bash
# 手动执行（等价于 cron 触发）
bash scripts/buffett_screen_cron.sh
```

## 安装 cron 任务

```bash
crontab -e
# 添加（交易日 16:10）：
10 16 * * 1-5 /path/to/trade-krono-cli/scripts/buffett_screen_cron.sh
```

> `1-5` 表示周一到周五。16:10 选在 15:00 收盘后，确保当日 K 线与基本面数据已同步完整。

## 日志

每次执行写入独立日志文件：

```
outputs/logs/buffett_screen_<YYYYMMDD_HHMMSS>.log
```

包含开始时间、筛选输出（stdout+stderr）、结束时间与成功/失败状态。

## 环境变量

| 变量 | 说明 |
|------|------|
| `HITHINK_FINANCE_API_KEY` | 同花顺 Fuyao API Key（筛选必需，从 `.env` 读取） |

## 退出码

- `0`：筛选成功
- 非 `0`：筛选失败（透传 Python 进程退出码）

## 注意事项

- 本脚本使用 `set -euo pipefail`，任何一步失败立即退出。
- cron 环境没有交互式 shell，脚本内显式 `export PATH` 确保能找到 `uv`。
- 全量筛选约 29 分钟（4966 只，20 并发），cron 触发后勿中途取消。
