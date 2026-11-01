# scripts/ Shell 脚本说明索引

`scripts/` 目录下所有 `.sh` 脚本的说明文档，每个脚本对应一篇 `.md`。

| 脚本 | 文档 | 用途 |
|------|------|------|
| `scripts/daily_flow.sh` | [daily_flow.md](daily_flow.md) | 每日投研流程薄壳入口（透传 env → `flow_runner.py run --all`） |
| `scripts/post_sync_export.sh` | [post_sync_export.md](post_sync_export.md) | 同步后共享数据导出（默认禁用，`--enable` 才执行） |
| `scripts/buffett_screen_cron.sh` | [buffett_screen_cron.md](buffett_screen_cron.md) | 巴菲特筛选 Cron 包装（交易日 16:10） |
| `scripts/cache_sync.sh` | [cache_sync.md](cache_sync.md) | 数据缓存检查/同步 Cron 包装（交易日 10:00 + 15:30） |
| `scripts/install.sh` | [install.md](install.md) | 一键安装（检查 Python / 装依赖 / 生成 .env 模板） |

## 相关文档

- **流程执行器**（check/sync/筛选 各 step 的统一调度、provider 配置）：[../flow_runner_guide.md](../flow_runner_guide.md)
- **项目总纲**（命令清单、数据流、关键规则）：[../../AGENTS.md](../../AGENTS.md)

## 约定

- 所有 `.sh` 脚本使用 `set -uo pipefail`（部分加 `-e`），cron 环境内显式 `export PATH` 以定位 `uv`。
- cron 包装脚本（`buffett_screen_cron.sh` / `cache_sync.sh`）实际逻辑在对应 Python 脚本，shell 只负责环境准备 + 日志落盘。
- 日志统一落在 `outputs/logs/`。
