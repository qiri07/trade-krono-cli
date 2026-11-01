# post_sync_export.sh — 同步后共享数据导出

## 作用

数据同步完成后，把 `pipeline_cache.db` 导出为多格式共享数据（CSV / Parquet / HDF5 / qlib / vnpy 等），供下游项目（qlib / vnpy / RD-Agent / backtrader / TradingAgents）通过 symlink 直接读取。

核心逻辑在 `scripts/export_shared_data.py`，本脚本是薄包装。

**导出默认禁用**：需显式传 `--enable` 才真正执行，否则直接跳过并退出（exit 0）。这是为了防止 `daily_flow.sh` / cron 每次同步后都重跑 3.6GB 的多格式导出。

## 用法

```bash
# 默认：跳过导出（安全默认）
bash scripts/post_sync_export.sh
# 输出：⏭️ 导出已默认禁用（未传 --enable），跳过导出。

# 实际执行导出
bash scripts/post_sync_export.sh --enable
```

## 导出目标目录

默认 `data/shared_data/`（相对项目根）。可重定向到其他位置：

```bash
# 导出到共享数据目录（供 7 个项目 symlink）
bash scripts/post_sync_export.sh --enable
# 等价于：
uv run python scripts/export_shared_data.py --format all --dest data/shared_data --enable
```

> 各下游项目通过 symlink 指向 `/run/media/onai/MyDisk/Work/shared_data/`（见 AGENTS.md「共享数据源机制」），无需复制数据。

## 导出格式

| 格式 | 路径 | 目标项目 |
|------|------|----------|
| CSV | `astock_daily_csv/` | Kronos / backtrader |
| Parquet | `astock_daily.parquet` | RD-Agent |
| HDF5 | `astock_daily.h5` | RD-Agent |
| qlib CSV | `astock_daily_qlib_csv/` | qlib dump_bin |
| qlib Binary | `qlib_data/` | qlib / RD-Agent |
| vnpy Parquet | `vnpy_daily/` | vnpy alpha |

## 在 flow_runner 中的位置

`post_sync_export.sh` 对应 `flow_runner.py` 的 `export` step（默认禁用）：

```bash
# 通过 flow_runner 触发
uv run python scripts/flow_runner.py run --all --enable export

# 或 env flag
EXPORT=1 bash scripts/daily_flow.sh
```

## 退出码

- `0`：导出成功，或（未传 `--enable` 时）正常跳过
- 非 `0`：`export_shared_data.py` 执行失败

## 注意事项

- 全量多格式导出约 3.6GB，耗时较长（数分钟到十几分钟），建议在数据同步后批量执行而非每次同步都跑。
- `--format all` 导出全部 7 种格式；`export_shared_data.py` 也支持单格式（`--format csv` 等），本脚本固定用 `all`。
