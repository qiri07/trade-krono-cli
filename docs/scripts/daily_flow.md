# daily_flow.sh — 每日投研流程（薄壳入口）

## 作用

一键执行每日投研流程。**本脚本已重构为薄壳**——内部不再内联各步骤命令，而是透传 env flag 并调用 `scripts/flow_runner.py run --all`，由统一 step 注册表（`flow_runner.py`）实际调度 13 个步骤。

完整 step 说明、CLI 参数、典型场景见 `docs/flow_runner_guide.md`。

## 用法

```bash
# 全量串行（默认 12 个 step；export 默认禁用）
bash scripts/daily_flow.sh

# 只打印不执行（dry-run）
DRY_RUN=1 bash scripts/daily_flow.sh

# 跳过飞书推送
NO_PUSH=1 bash scripts/daily_flow.sh

# 跳过数据同步类 step（check/sync/sync_etf/metadata）
NO_SYNC=1 bash scripts/daily_flow.sh

# 跳过 ETF 同步
SKIP_ETF=1 bash scripts/daily_flow.sh

# 额外执行共享数据导出
EXPORT=1 bash scripts/daily_flow.sh

# 组合
NO_SYNC=1 SKIP_ETF=1 DRY_RUN=1 bash scripts/daily_flow.sh
```

## 环境 flag（透传给 flow_runner.py）

| 环境变量 | 默认 | 效果 | 等效 CLI |
|----------|------|------|----------|
| `DRY_RUN` | 0 | =1 时只打印命令不执行 | `--dry-run` |
| `NO_PUSH` | 0 | =1 时关闭飞书推送（`PUSH_FLAG=0`） | — |
| `NO_SYNC` | 0 | =1 时跳过 `sync`/`sync_etf`/`metadata` | `--disable sync,sync_etf,metadata` |
| `SKIP_ETF` | 0 | =1 时跳过 `sync_etf` | `--disable sync_etf` |
| `EXPORT` | 0 | =1 时启用 `export` step | `--enable export` |

> 这些 flag 在 `flow_runner.py` 模块加载时读取，由 `daily_flow.sh` 的 `export` 行透传，无需在命令行额外指定。

## 执行顺序

```
check → sync → sync_etf → metadata → buffett → resonance → multi_head
→ whitelist → double_ma → macd_ma → combined → intraday  [→ export（需开启）]
```

`flow_runner.py` 按 `depends_on` 拓扑序执行，单步失败默认「记录但继续」，`--strict` 时首错即停。

## 等价直接调用

`daily_flow.sh` 本质上就是下面这行的简写：

```bash
uv run python scripts/flow_runner.py run --all
# 加 --dry-run（DRY_RUN=1 时）/ --enable export（EXPORT=1 时）等
```

需要单独跑某一步、或临时增删 step 时，直接用 `flow_runner.py` 更灵活：

```bash
uv run python scripts/flow_runner.py list                       # 查看所有 step
uv run python scripts/flow_runner.py run --step check           # 单步
uv run python scripts/flow_runner.py run --all --disable intraday
```

## 退出码

- `0`：全部默认 step 成功
- `1`：有 step 失败（非 strict 模式会跑完所有 step 后返回 1）
- `2`：参数错误（无 step 可执行等）

## 历史说明

本脚本原先内联 10 个步骤命令（~120 行）。2026-10-30 重构为薄壳（~30 行），把步骤注册、依赖管理、enable/disable 逻辑统一收敛到 `scripts/flow_runner.py`，新增/删除 step 只需改注册表，本文件无需再动。
