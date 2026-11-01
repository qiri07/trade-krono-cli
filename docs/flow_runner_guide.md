# 每日投研流程使用指南（flow_runner）

> 本指南覆盖 `scripts/flow_runner.py`（统一 step 注册表）、`scripts/check_data_status.py`（数据现状检查 + 补拉计划）、`scripts/fill_missing_and_today.py`（增量补拉）、`configs/providers.yaml`（provider 配置）四个新增/改造组件的用法。
>
> 最后更新：2026-10-30

---

## 目录

- [1. 快速上手](#1-快速上手)
- [2. flow_runner.py — 统一 step 注册表](#2-flow_runnerpy--统一-step-注册表)
- [3. check_data_status.py — 数据现状检查 + 补拉计划](#3-check_data_statuspy--数据现状检查--补拉计划)
- [4. fill_missing_and_today.py — 增量补拉](#4-fill_missing_and_todaypy--增量补拉)
- [5. configs/providers.yaml — Provider 配置](#5-configsprovidersyaml--provider-配置)
- [6. daily_flow.sh — 兼容入口](#6-daily_flowsh--兼容入口)
- [7. 典型场景](#7-典型场景)
- [8. 故障排查](#8-故障排查)

---

## 1. 快速上手

```bash
# 查看全部可执行 step（含默认启用状态）
uv run python scripts/flow_runner.py list

# 单独执行某个 step（如「检查数据现状 + 生成补拉计划」）
uv run python scripts/flow_runner.py run --step check

# 单独执行「增量补拉」（读取 check 产出的 sync_plan JSON）
uv run python scripts/flow_runner.py run --step sync

# 全量串行执行所有默认开启 step（12 个；export 默认禁用）
uv run python scripts/flow_runner.py run --all

# dry-run 预览（只打印命令，不实际执行）
uv run python scripts/flow_runner.py run --all --dry-run
```

---

## 2. flow_runner.py — 统一 step 注册表

### 2.1 全部 step

| Step | 描述 | 默认 | 命令 |
|------|------|------|------|
| `check` | 数据现状检查 + 补拉计划 | ✅ | `scripts/check_data_status.py` |
| `sync` | K 线增量补拉 | ✅ | `scripts/fill_missing_and_today.py` |
| `sync_etf` | ETF K 线同步（增量） | ✅ | `scripts/etf_history_sync.py` |
| `metadata` | 参考数据 TTL 刷新 | ✅ | `scripts/fetch_reference_data.py` |
| `buffett` | 巴菲特六闸门筛选 | ✅ | `tests/buffett_screen_parallel.py` |
| `resonance` | 三线共振扫描 | ✅ | `scripts/multi_line_resonance.py` |
| `multi_head` | 多头排列扫描 | ✅ | `scripts/multi_scan.py` |
| `whitelist` | 白名单分析 + AI + 飞书 | ✅ | `scripts/daily_analysis.py` |
| `double_ma` | 双均线金叉（完整模式） | ✅ | `scripts/double_ma_golden_cross.py` |
| `macd_ma` | MACD+均线筛选 | ✅ | `scripts/macd_ma_screener.py --push $PUSH_FLAG` |
| `combined` | 综合交集汇总 | ✅ | `scripts/combined_stock_analysis.py --push $PUSH_FLAG` |
| `intraday` | 盘中技术信号扫描（不推送） | ✅ | `scripts/intraday_scan.py --push 0` |
| `export` | 共享数据导出 | ❌ | `bash scripts/post_sync_export.sh` |

> `export` 默认禁用，需 `--enable export` 或 `EXPORT=1` 显式开启。

### 2.2 CLI 参数

```bash
# 列出全部 step
uv run python scripts/flow_runner.py list

# 单步执行
uv run python scripts/flow_runner.py run --step <name>

# 全量串行（默认开启 12 个 step）
uv run python scripts/flow_runner.py run --all

# 临时启用默认关闭的 step
uv run python scripts/flow_runner.py run --all --enable export

# 临时排除某 step
uv run python scripts/flow_runner.py run --all --disable intraday

# 同时启用 + 排除
uv run python python scripts/flow_runner.py run --all --enable export --disable intraday

# dry-run（只打印命令，不实际执行）
uv run python scripts/flow_runner.py run --all --dry-run

# 严格模式（--all 时任一 step 失败立即停止）
uv run python scripts/flow_runner.py run --all --strict
```

### 2.3 环境 flag 透传（与旧 `daily_flow.sh` 兼容）

| 环境变量 | 效果 | 等效 CLI |
|----------|------|----------|
| `DRY_RUN=1` | 只打印不执行 | `--dry-run` |
| `NO_PUSH=1` | 关闭飞书推送（`PUSH_FLAG=0`） | — |
| `NO_SYNC=1` | 跳过 `sync` / `sync_etf` / `metadata` | `--disable sync,sync_etf,metadata` |
| `SKIP_ETF=1` | 跳过 `sync_etf` | `--disable sync_etf` |
| `EXPORT=1` | 启用 `export` | `--enable export` |

env flag 在 `flow_runner.py` 模块加载时读取，修改后需重新 `uv run` 才生效。

### 2.4 执行语义

- **`--all` 默认行为**：按依赖拓扑序串行执行，单步失败「记录但继续」（返回码 1）。
- **`--all --strict`**：首错即停，立即返回该步退出码。
- **单步 `--step X`**：只执行 X，不影响其他 step。
- 每步的退出码非 0 时，`flow_runner` 打印 `✗ [step] 失败（exit N）`，但不中断后续（非 strict 模式）。

---

## 3. check_data_status.py — 数据现状检查 + 补拉计划

### 3.1 作用

检查 K 线缓存与 `metadata.json` 的当前状态，生成**结构化补拉计划**（JSON），供 `fill_missing_and_today.py --plan` 读取决定「补哪些票、是否覆盖」。

### 3.2 用法

```bash
# 生成补拉计划（含人类可读报告 + JSON 文件）
uv run python scripts/check_data_status.py

# 跳过人类可读报告，只生成 JSON
uv run python scripts/check_data_status.py --no-integrity-report

# 调整 metadata TTL 阈值（默认 24h）
uv run python scripts/check_data_status.py --ttl-hours 48

# 通过 flow_runner 执行（推荐，与 sync 衔接更自然）
uv run python scripts/flow_runner.py run --step check
```

### 3.3 输出文件

`outputs/results/sync_plan_<YYYYMMDD>.json`

```json
{
  "generated_at": "2026-10-30T12:00:00",
  "kline": {
    "total_records": 6954,
    "total_tickers": 6954,
    "latest_date": "2026-10-29",
    "expected_date": "2026-10-29",
    "is_up_to_date": true,
    "failed_tickers": ["sh.600519", "..."],
    "need_today_tickers": ["sz.000001", "..."]
  },
  "metadata": {
    "file": "outputs/cache/metadata.json",
    "entries": 6954,
    "age_hours": 2.5,
    "ttl_hours": 24.0,
    "is_stale": false,
    "needs_full_refresh": false
  },
  "plan": {
    "fetch_kline": {
      "tickers": ["sh.600519", "sz.000001"],
      "reasons": { "failed": ["sh.600519"], "need_today": ["sz.000001"] }
    },
    "fetch_metadata": { "mode": "skip", "tickers": [] }
  }
}
```

| 字段 | 说明 |
|------|------|
| `kline.failed_tickers` | 记录数 < 100 的非北交所票（历史同步失败，需补全量） |
| `kline.need_today_tickers` | 缺少最新交易日 K 线的票（需补增量） |
| `metadata.needs_full_refresh` | 条目 < 100 或 age > TTL 时为 `true`（全量重拉） |
| `plan.fetch_metadata.mode` | `full`（全量）/ `partial`（部分）/ `skip`（新鲜跳过） |

---

## 4. fill_missing_and_today.py — 增量补拉

### 4.1 新增 CLI 参数

```bash
# 全库扫描（默认行为，与历史一致）
uv run python scripts/fill_missing_and_today.py

# 读取 check 产出的补拉计划（只拉计划中列出的票）
uv run python scripts/fill_missing_and_today.py --plan outputs/results/sync_plan_20261030.json

# dry-run（只打印补拉清单，不写缓存）
uv run python scripts/fill_missing_and_today.py --plan outputs/results/sync_plan_20261030.json --dry-run

# 全量覆盖（重拉完整历史段，覆盖现有记录；默认是增量合并）
uv run python scripts/fill_missing_and_today.py --plan <path> --no-incremental
```

| 参数 | 说明 |
|------|------|
| `--plan <path>` | 读取 JSON 补拉计划，只拉计划中列出的票；缺省时全库扫描 |
| `--dry-run` | 打印补拉清单后直接返回，不实例化 factory、不写库 |
| `--no-incremental` | 禁用增量合并（`pd.concat + drop_duplicates`），改为全量覆盖 |

> **默认行为（无 `--no-incremental`）**：增量合并——仅追加最新 K 线到现有记录，不覆盖历史，避免「伪除权跳变」。

### 4.2 通过 flow_runner 执行

```bash
# 先跑 check 生成计划，再跑 sync（自动衔接）
uv run python scripts/flow_runner.py run --step check
uv run python scripts/flow_runner.py run --step sync

# 或一次性全量（check → sync → sync_etf → metadata → ...）
uv run python scripts/flow_runner.py run --all
```

---

## 5. configs/providers.yaml — Provider 配置

### 5.1 文件位置与结构

```
configs/providers.yaml
```

```yaml
defaults:
  sh: [baostock, tonghuashun, mootdx]
  sz: [baostock, tonghuashun, mootdx]
  bj: [tonghuashun, baostock]
  hk: [akshare_hk, tonghuashun, mootdx, akshare]

etf:
  sh: [baostock, tonghuashun, mootdx]
  sz: [baostock, tonghuashun, mootdx]

override:
  # 单只票精确覆盖（可选）
  sh.600519: [tonghuashun, baostock]

fetch:
  timeout_seconds: 45
  workers: 8
```

### 5.2 解析优先级（`_utils.get_provider_chain`）

```
1. override[ticker]          ← 单只票精确覆盖
2. etf[ticker_type]         ← ETF（is_etf_ticker）
3. defaults[ticker_type]    ← 普通票按前缀 sh/sz/bj/hk
4. 内置硬编码链             ← YAML 缺失/损坏时兜底
```

### 5.3 受影响的脚本

以下脚本的 provider 路由现在统一读 `configs/providers.yaml`（无需改代码）：

| 脚本 | 调用方式 |
|------|----------|
| `scripts/fill_missing_and_today.py` | `get_provider_chain(ticker)` |
| `scripts/etf_history_sync.py` | `get_provider_chain(ticker)`（原写死链已移除） |
| `scripts/full_history_sync.py` | `get_provider_chain(ticker)` |
| `scripts/full_history_from_2015.py` | `get_provider_chain(ticker)` |
| `scripts/retry_missing_history.py` | 内部 `get_provider_chain` |
| `scripts/incremental_sync_latest.py` | `get_provider_chain(ticker)` |
| `scripts/fill_missing_latest.py` | `get_provider_chain(ticker)` |
| `scripts/fill_bj_hk.py` | `get_provider_chain(ticker)` |

### 5.4 修改示例

```yaml
# 让沪市股票优先用 tonghuashun（同花顺数据质量更好时）
defaults:
  sh: [tonghuashun, baostock, mootdx]   # 改这一行即可
```

修改后所有同步脚本下次运行即生效，无需重启。

### 5.5 回退行为

`configs/providers.yaml` **不存在**或**解析失败**（YAML 语法错误 / 非 dict）时，`load_provider_config()` 静默回退到内置硬编码链（与历史行为一致），不 raise，同步脚本仍可运行。

---

## 6. daily_flow.sh — 兼容入口

`scripts/daily_flow.sh` 已改为**薄壳**，内部调用 `flow_runner.py run --all`，env flag 自动透传：

```bash
# 全量串行（与历史行为一致，12 个 step，export 默认禁用）
bash scripts/daily_flow.sh

# 跳过数据同步
NO_SYNC=1 bash scripts/daily_flow.sh

# 跳过 ETF 同步
SKIP_ETF=1 bash scripts/daily_flow.sh

# 开启共享数据导出
EXPORT=1 bash scripts/daily_flow.sh

# 只打印不执行
DRY_RUN=1 bash scripts/daily_flow.sh

# 组合
NO_SYNC=1 SKIP_ETF=1 DRY_RUN=1 bash scripts/daily_flow.sh
```

> 薄壳层只做 env 透传 + 调用 `flow_runner.py`，不再内联 10 个步骤命令。新增/删除 step 只需改 `flow_runner.py` 的 `STEPS` 注册表，`daily_flow.sh` 无需修改。

---

## 7. 典型场景

### 7.1 日常全流程（含数据同步）

```bash
# 最简：一行跑完
bash scripts/daily_flow.sh

# 或等价
uv run python scripts/flow_runner.py run --all
```

执行顺序：`check → sync → sync_etf → metadata → buffett → resonance → multi_head → whitelist → double_ma → macd_ma → combined → intraday`

### 7.2 数据已同步，只跑筛选

```bash
NO_SYNC=1 bash scripts/daily_flow.sh
# 或
uv run python scripts/flow_runner.py run --all --disable check,sync,sync_etf,metadata
```

### 7.3 先检查再补拉（分离 check / sync）

```bash
# 第一步：检查数据现状，生成补拉计划
uv run python scripts/flow_runner.py run --step check

# 查看计划
cat outputs/results/sync_plan_$(date +%Y%m%d).json

# 第二步：按计划增量补拉（不覆盖全量）
uv run python python scripts/flow_runner.py run --step sync
# 或直接调脚本（带 plan）
uv run python scripts/fill_missing_and_today.py \
  --plan outputs/results/sync_plan_$(date +%Y%m%d).json
```

### 7.4 修改 provider 优先级

```bash
# 1. 编辑配置
vim configs/providers.yaml
# 例如把 sh 改为 tonghuashun 优先：
#   defaults:
#     sh: [tonghuashun, baostock, mootdx]

# 2. 验证（dry-run，不实际拉数据）
uv run python scripts/fill_missing_and_today.py --dry-run

# 3. 正式拉取
uv run python scripts/flow_runner.py run --step sync
```

### 7.5 开启共享数据导出（供 qlib/vnpy/RD-Agent）

```bash
# 单次导出
EXPORT=1 bash scripts/daily_flow.sh

# 或单独导出
uv run python scripts/flow_runner.py run --all --enable export

# 或直接调
bash scripts/post_sync_export.sh
```

### 7.6 盘中单独跑扫描

```bash
uv run python scripts/flow_runner.py run --step intraday
# 或
uv run python scripts/intraday_scan.py --mode resonance --min-signals 2
```

---

## 8. 故障排查

### 8.1 `flow_runner.py run --step X` 报「未知 step」

```bash
# 查看可用 step 名称
uv run python scripts/flow_runner.py list
```

step 名称区分大小写，必须是 `STEPS` 注册表里的精确值。

### 8.2 某 step 失败但后续仍执行

这是**预期行为**（非 strict 模式）。要首错即停：

```bash
uv run python scripts/flow_runner.py run --all --strict
```

### 8.3 `export` 始终没跑

`export` 默认 `enabled_by_default=False`。需显式开启：

```bash
EXPORT=1 bash scripts/daily_flow.sh
# 或
uv run python scripts/flow_runner.py run --all --enable export
```

### 8.4 修改 `configs/providers.yaml` 后不生效

确认文件路径正确（项目根目录下 `configs/providers.yaml`），且格式为合法 YAML。解析失败时会**静默回退**到硬编码链（不报错），可用以下方式验证：

```bash
uv run python -c "
from scripts._utils import load_provider_config
cfg = load_provider_config(force_reload=True)
print(cfg['defaults'])
"
```

若输出 `{'sh': ['baostock', 'tonghuashun', 'mootdx'], ...}`，说明读到了；若输出硬编码值，说明 YAML 有问题。

### 8.5 `check_data_status.py` 报 `kline_cache` 表不存在

确认 `outputs/cache/pipeline_cache.db` 存在且已初始化。可用：

```bash
uv run python scripts/check_data_integrity.py
```

先做一次完整性诊断，再运行 `check_data_status.py`。

### 8.6 metadata.json 条目数异常（< 100）

`check_data_status.py` 会标记 `needs_full_refresh=true`。直接强制全量刷新：

```bash
uv run python scripts/fetch_reference_data.py --force-refresh
# 约 50 分钟完成 6954 只
```

---

## 附：文件清单

| 文件 | 类型 | 说明 |
|------|------|------|
| `scripts/flow_runner.py` | 新建 | 统一 step 注册表 + 执行器 |
| `scripts/check_data_status.py` | 新建 | 数据现状检查 + 补拉计划生成 |
| `configs/providers.yaml` | 新建 | Provider 优先级链配置 |
| `scripts/_utils.py` | 改造 | 新增 `load_provider_config` / `get_provider_chain` 改造 |
| `scripts/fill_missing_and_today.py` | 改造 | 新增 `--plan` / `--dry-run` / `--no-incremental` |
| `scripts/etf_history_sync.py` | 改造 | 移除硬编码 provider 链，改用 `get_provider_chain` |
| `scripts/daily_flow.sh` | 薄壳化 | 改为调用 `flow_runner.py run --all` |
| `tests/test_flow_runner.py` | 新建 | flow_runner 测试（21 用例） |
| `tests/test_check_data_status.py` | 新建 | check_data_status 测试（6 用例） |
| `tests/test_fill_missing_and_today.py` | 新建 | fill_missing_and_today 测试（7 用例） |
| `tests/test_scripts_utils.py` | 扩展 | YAML provider 配置测试（+6 用例） |
