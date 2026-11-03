# CZSC 全市场信号扫描集成

本目录包含将 czsc（缠论技术分析库）集成到 trade-krono-cli 的全市场信号扫描任务。

## 核心文件

| 文件 | 用途 |
|------|------|
| `czsc_universe_signal_scan.py` | 核心任务脚本：DB→parquet 增量转换 + 全市场信号计算 + 覆盖度校验 |
| `czsc_signal_scan_cron.sh` | Cron 包装脚本，每个交易日 16:20 自动执行 |

## 数据流

```
pipeline_cache.db (kline_cache, 1.1 GB, pandas 3.x pickle)
    │
    ▼ 增量转换（只转有更新的标的，约 0 秒增量 / 94 秒全量）
outputs/czsc_bars/*.parquet (每标的一个，标准 8 列 OHLCV)
    │
    ▼ 并行信号计算（16 进程，约 60 秒）
outputs/czsc_signal_scan/matched_<preset>_<date>.csv
outputs/czsc_signal_scan/summary_<date>.json
```

## 信号预设

| 预设 | 信号配置 | 条件 | 典型命中 |
|------|----------|------|----------|
| `bi_ma` | 笔状态 + 20日SMA + 涨跌停 | 笔向上 AND SMA20多头 AND NOT 跌停 | ~486/6822 |
| `third_buy` | 三买 + 笔状态 | 三买 AND 笔向上 | ~40/6822 |

## 关键参数

```python
MIN_BARS = 60        # 少于此行数跳过（新股）
INIT_N = 250         # 预热 K 线数（默认 500 对短历史不友好）
WORKERS = 16         # 并行进程数（自动检测 CPU 核心）
VALID_PREFIXES = ("sh.", "sz.", "bj.")  # 过滤 hk 等
```

## 命令行用法

```bash
# 仅增量转换 DB→parquet
uv run python scripts/czsc_universe_signal_scan.py --convert-only

# 仅扫描（用已有 parquet，不转换）
uv run python scripts/czsc_universe_signal_scan.py --scan-only --asof 2026-10-30

# 全流程（增量转换 + 扫描 + 覆盖度）
uv run python scripts/czsc_universe_signal_scan.py --asof 2026-10-30
```

## 产物说明

- `matched_bi_ma_YYYYMMDD.csv`：预设 A 命中股票 + 日期 + 收盘价 + 触发信号键值
- `matched_third_buy_YYYYMMDD.csv`：预设 B 命中
- `summary_YYYY-MM-DD.json`：汇总（命中数、耗时、覆盖率、转换统计）

## 覆盖度校验

每次扫描自动统计：当日有行情的标的数（active）、命中数、命中率。

```
2026-10-30: active=2308, hits=180 (预设A+预设B去重), rate=7.80%
```

注意：DB 中各标的最新交易日不一致（北交所多为 10-27，主板多为 10-30），所以 as-of 扫描只统计「≤ 目标日」有数据的标的。

## 依赖与兼容性

- **必须用 trade-krono-cli 的 `.venv` 解释器（pandas 3.0.5）**，因为 DB blob 是 pandas 3 的 `datetime64[us]` pickle，czsc venv（pandas 2.3.2）无法反序列化。
- czsc 库通过 `uv pip install -e /path/to/czsc` 安装到 `.venv`。

## 自动化部署

```bash
# 1) 安装 czsc 到 trade-krono-cli 环境
uv pip install -e /run/media/onai/MyDisk/Work/czsc

# 2) 加入 crontab（交易日 16:20）
crontab -e
# 20 16 * * 1-5 /run/media/onai/MyDisk/Work/trade-krono-cli/scripts/czsc_signal_scan_cron.sh

# 3) 首次全量转换（约 94 秒）
uv run python scripts/czsc_universe_signal_scan.py --convert-only
```

## 测试验证记录

| 日期 | 预设 | universe | hits | active | hit_rate | 耗时 |
|------|------|----------|------|--------|----------|------|
| 2026-10-30 | bi_ma | 6822 | 486 | 2308 | 7.80% | 52.3s |
| 2026-10-30 | third_buy | 6822 | 40 | - | - | 33.7s |

校验结果：
- ✅ DB→parquet 逐行逐值保真（随机 11 只，15,241 行）
- ✅ 无未来函数：as-of 结果 == 只用当日及以前数据重算（30/30 通过）
- ✅ 串行 vs 并行一致（200/200 通过）
- ✅ 命中包含纯股票 412 只 + ETF 75 只

## 目录结构

```
outputs/
├── czsc_bars/              # parquet 缓存（增量更新）
│   ├── sh.600000.parquet
│   └── ...
├── czsc_signal_scan/       # 每日扫描产物
│   ├── matched_bi_ma_20261101.csv
│   ├── matched_third_buy_20261101.csv
│   └── summary_2026-10-30.json
└── logs/
    └── czsc_signal_scan_YYYYMMDD_HHMMSS.log
```