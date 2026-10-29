# AGENTS.md — trade-krono-cli 项目宪法

> AI 编程助理会话开始时自动加载。所有生成/修改的代码必须遵守本文件。

## 项目概览
- **用途**：A 股投研+预测流水线 — 并行调用 TradingAgents-astock（多 Agent 深度分析）和 Kronos（K 线序列预测），融合打分输出推荐报告
- **语言**：Python 3.10+
- **包管理**：`uv`（禁止 `pip` / `poetry` / `virtualenv`）
- **CLI 框架**：Typer + Rich
- **测试**：pytest（全绿门槛）
- **数据存储**：SQLite（`outputs/cache/*.db`）、JSON/HTML 报告（`outputs/results/`）、日志（`outputs/pipeline.log/.json`）

## 命令（Commands）
```bash
uv sync                          # 安装全部依赖（含 dev）
uv add <pkg>                     # 添加运行时依赖
uv add --dev <pkg>               # 添加开发依赖
uv run trade-krono-cli run --tickers "600519,000858" --date 2026-08-11  # 一键运行（TA + Kronos 并行）
uv run trade-krono-cli ta --tickers "600519"                           # 仅 TA 分析
uv run trade-krono-cli kronos --tickers "600519"                       # 仅 Kronos 预测
uv run trade-krono-cli sync-whitelist                  # 仅同步白名单股票
uv run python tests/buffett_screen_parallel.py         # 巴菲特六闸门全量筛选（并行版，仅筛选+AI核实）
uv run python scripts/buffett_daily.py                 # 巴菲特筛选+技术分析+逐只飞书推送（统一版，推荐）
uv run python scripts/full_history_from_2015.py        # 全量历史同步（2015起，~30分钟）
uv run python scripts/full_history_sync.py             # 全量历史同步（2020起，~22分钟）
uv run python scripts/retry_missing_history.py         # 重试缺失历史的股票
uv run python scripts/fill_missing_and_today.py        # 补齐失败股票 + 同步今日增量（日期动态检测）
uv run python scripts/etf_history_sync.py               # ETF K线同步（自动发现~1409只，默认今日增量）
uv run python scripts/etf_history_sync.py --full        # ETF 全量历史同步（2020起，~15-30分钟）
uv run python scripts/etf_history_sync.py --codes "510300,159915"  # 指定ETF代码同步
uv run python scripts/check_data_integrity.py          # 数据完整性检查
uv run python scripts/intraday_scan.py                 # 盘中技术信号扫描（白名单，全 4 类信号）
uv run python scripts/intraday_scan.py --mode resonance --min-signals 2  # 指定信号类型 + 命中阈值
uv run python scripts/cleanup_duplicates.py            # 清理重复记录
uv run python scripts/daily_analysis.py                # 每日白名单分析 + AI 验证 + 飞书推送
uv run python scripts/fetch_reference_data.py          # 批量获取股票参考数据（PE/PB/行业等）
uv run python scripts/double_ma_golden_cross.py         # 双均线金叉统一筛选（默认完整模式：日+周MA5>MA13，PE∈(0,30)，MACD>0，ROE>15%，净利润>1亿）
uv run python scripts/double_ma_golden_cross.py --lite   # 轻量模式（仅技术分析：MA+PE+MACD，原 double_ma_screener 功能）
uv run python scripts/combined_stock_analysis.py          # 第9步：多线共振交集汇总 + 技术分析 + AI + 飞书（--min-sources 2 --push 1）
uv run python scripts/export_shared_data.py --format all --dest /run/media/onai/MyDisk/Work/shared_data  # 导出共享数据
uv run python scripts/export_vnpy_data.py              # 导出 vnpy 格式
bash scripts/post_sync_export.sh                       # 同步后一键导出所有格式
bash scripts/daily_flow.sh                             # 一键执行今日 10 步投研流程（同步+筛选+交集汇总+飞书；不含数据导出；NO_PUSH=1 关推送，NO_SYNC=1 跳同步，SKIP_ETF=1 跳ETF，EXPORT=1 加导出，DRY_RUN=1 只打印）
uv run ruff check .                  # Lint 检查
uv run ruff check --fix .            # Lint + 自动修复
uv run mypy .                        # 类型检查
uv run pytest                        # 跑全部测试
uv run pytest -x                     # 首个失败即停
```
提交前必须全绿：`uv run ruff check . && uv run mypy . && uv run pytest`

## 项目结构
```
trade_krono_cli/
├── cli.py                # Typer CLI 入口（app / repo_app 注册、命令分发）
├── cli_commands.py       # 向后兼容薄包装（DeprecationWarning）
├── cli_commands/         # CLI 命令包
│   ├── __init__.py       # 统一导出 run/ta/kronos/repo_* 等
│   ├── _core_commands.py # run/ta/kronos 主流程命令实际实现
│   ├── _core_helpers.py  # 共享工具函数（_load_env / _load_tickers / _sanitize_path）
│   ├── _sync_helpers.py  # 同步辅助函数
│   ├── core.py           # 薄包装（re-export）
│   ├── repo.py           # repo status/doctor/update/pin 子命令
│   ├── maintenance.py    # 向后兼容薄包装（re-export 子模块）
│   ├── maintenance_status.py   # status 命令
│   ├── maintenance_cache.py    # clear-cache / warm-cache 命令
│   ├── maintenance_sync.py     # sync-universe / sync-whitelist + _resolve_tickers
│   ├── maintenance_history.py  # history 命令
│   ├── maintenance_eval.py     # eval-prediction 命令
│   ├── maintenance_retry.py    # retry-failed 命令
│   ├── rank_providers.py     # provider 排名命令
│   ├── sync_universe.py      # sync-universe 命令
│   ├── sync_whitelist.py     # sync-whitelist 命令
│   └── export_daily_pv.py    # export-daily-pv 命令
├── config.py             # Settings dataclass：从 .env 加载，模块级单例 get_settings()
├── errors.py             # 异常层次：TradeKronoError → ModuleError / DataError / ModelError 等
├── logger.py             # loguru 初始化（控制台 + 文本日志 + JSON 结构化日志）
├── globals.py            # 全局单例清理工具（测试隔离用 clear_all_globals）
├── security.py           # 敏感信息脱敏（sanitize_for_log）
├── domain/               # 领域层（无外部依赖）：types / decision / signal / risk / prediction
├── data_providers/       # 多数据源抽象层（DataProvider ABC + KlineData/RealtimeQuote/StockMetadata）
│   ├── base.py / factory.py / baostock_provider.py / akshare_provider.py / mootdx_provider.py / tushare_provider.py / tonghuashun_provider.py
├── pipeline/             # 流水线编排：orchestrator（QuantPipeline + PipelineFactory）/ merge / reporter / stream_pipeline
├── models/               # Session 封装：ta_session.py / kronos_session.py
├── adapters/             # 外部项目适配器：kronos.py / tradingagents.py
├── risk/ scoring/ universe/ configs/  # 风险引擎 / 打分插件 / 股票池 / 子配置
├── research_db/          # 投研数据库包（向后兼容：research_db.py 为薄包装）
│   ├── __init__.py       # 组装 ResearchDatabase（MRO mixin 模式）+ 单例
│   ├── schema.py         # CREATE TABLE SQL + 常量 + 表名白名单
│   ├── migrations.py     # 向后兼容 schema 迁移逻辑
│   ├── base.py           # ResearchDatabase 基类（连接管理、建表、迁移）
│   ├── jobs.py           # Jobs 表 CRUD
│   ├── ta_analysis.py    # TA Analysis 表读写
│   ├── kronos_forecast.py # Kronos Forecast 表读写
│   ├── signals.py        # Signals 表读写
│   ├── decisions.py      # Decisions + Reports 表读写
│   ├── stats.py          # Stats / query_history / get_latest_signal_for_ticker
│   ├── committee.py      # Committee Deliberations 表读写
│   ├── strategy_runs.py  # Strategy Runs 表读写
│   ├── snapshots.py      # Data Snapshots 表读写
│   ├── walkforward.py    # Walk-Forward Runs 表读写
│   └── experiments.py    # Experiments 表读写

├── universe/             # 前置股票池过滤引擎
│   ├── provider.py       # UniverseProvider ABC + AkshareUniverseProvider / MootDxUniverseProvider / TongHuaShunUniverseProvider
│   ├── engine.py         # UniverseEngine（多阶段管道编排）
│   └── stages/           # 过滤阶段
│       ├── __init__.py   # FilterStage ABC
│       ├── static.py     # ST/停牌/次新/低价股静态过滤
│       ├── fundamental.py # PE/PB/市值/行业基本面过滤
│       ├── factor.py     # 量比/换手率流动性过滤
│       └── rules.py      # 自定义规则链过滤（FilterRulesStage）

external/TradingAgents-astock | external/Kronos  # 符号链接，gitignore
tests/buffett_screen.py            # 巴菲特六闸门筛选器（串行版，原始脚本）
tests/buffett_screen_parallel.py   # 巴菲特六闸门筛选器（并行版，20 并发 — 仅筛选+AI核实，不含技术分析）
scripts/buffett_daily.py           # 巴菲特筛选+技术分析+飞书推送（合并版，推荐使用）
tests/buffett_cache.py             # 缓存层：SQLite 初始化 / 读写 / TTL / 清理
tests/buffett_screening.py         # 筛选域：API 客户端、五闸门逻辑、三项深度验证、StockMetrics
tests/check_cache_integrity.py     # 缓存完整性检查工具
tests/check_cache_quality.py       # 缓存质量分析工具
tests/test_sync_whitelist.py       # sync-whitelist / sync-universe 单元测试
tests/conftest.py                  # 共享 fixture（make_mock_settings）+ pytest_configure/env var 路由 + clear_all_globals hook
tests/test_*.py                    # 扁平化测试结构：每个源模块对应一个测试文件（2766 个测试）
outputs/                     # 运行时产物（gitignore）
outputs/cache/               # K 线缓存数据库（pipeline_cache.db，约 588 MB）
outputs/cache/backups/       # 数据库备份（每次大操作前自动创建）
outputs/results/             # 报告输出目录（gitignore 中 *.db/*.log，结果文件可提交）
scripts/                     # 运维脚本（数据同步 / 完整性检查 / 飞书通知 / 共享数据导出）
scripts/export_shared_data.py  # 共享数据导出（pipeline_cache.db → CSV/Parquet/HDF5/qlib 等 7 种格式）
scripts/export_vnpy_data.py    # vnpy parquet 格式导出（CSV → parquet，供 vnpy alpha 使用）
scripts/post_sync_export.sh    # 同步后一键导出全部格式到 /run/media/onai/MyDisk/Work/shared_data/
```

### 核心数据流
```
[tickers + date]
  → [UniverseEngine]（可选自动发现，多阶段过滤）
       ├─ StaticFilterStage    → 排除 ST / 停牌 / 次新 / 低价股
       ├─ FundamentalFilterStage → 排除 PE/PB/市值异常（含 min_pb 资不抵债过滤）
       ├─ FilterRulesStage      → 应用自定义规则链（filter_rules）
       └─ FactorFilterStage     → 排除低流动性
  → [DataProviders]（baostock/akshare/mootdx/tushare/tonghuashun 主备降级）
  → [并行执行] ThreadPoolExecutor(max_workers=2)：TA 分析 + Kronos 预测
  → [StockFilter]（后验：置信度/信号/风险分过滤）
  → [merge_results]（TA + Kronos 融合打分，含 T+1 约束）
  → [InvestmentCommittee]（委员会审议 Bull/Bear Case）
  → [ResearchDB]（写入 Job/TA/Kronos/Decision/Committee）
  → [Reporters]（JSON + HTML 落盘 → outputs/results/）
```

## 关键规则（Critical Rules）
1. 所有命令前缀 `uv run` —— 禁止裸 `python`/`pytest`
2. 添加依赖只能用 `uv add`，禁止手编辑 `pyproject.toml` 的 dependencies，禁止创建 `requirements.txt`
3. **所有函数签名必须有类型注解**；模块顶部加 `from __future__ import annotations`
4. 使用现代类型语法：`X | None` 而非 `Optional[X]`；`list[str]` 而非 `List[str]`
5. 禁止 `Any`，除非有注释说明原因；禁止 `# type: ignore`（除非附具体错误码与理由）
6. 测试一律用 pytest，禁止 `unittest.TestCase`
7. 库代码禁止 `print()`，必须用 `loguru.logger`（`from loguru import logger`）
8. 错误处理：捕获具体异常，禁止 `except Exception: pass`
9. **日志规范**：关键节点 `info`，异常 `error` 并附带上下文（ticker、日期、错误码）；禁止输出 API Key/Token；统一使用 `logger.py.setup_logger()` 初始化
10. **密钥安全**：LLM API Key 从 `.env` 读取，禁止硬编码；测试中通过 `make_mock_settings` 注入
11. **测试隔离**：`conftest.py` 的 `pytest_configure` hook 自动设置 `TRADING_KRONO_CACHE_DIR` / `TRADING_KRONO_RESULTS_DIR` 指向临时目录；`pytest_sessionstart` hook 启动时校验隔离状态；`Cache` / `ResearchDatabase` 初始化时调用 `_validate_test_isolation()` 守卫函数，拒绝写入生产路径；`pytest_runtest_setup/call` hook 自动调用 `clear_all_globals()` 清除全局单例；禁止直接引用 `config._settings`
12. **公共函数/类必须有 Google 风格 docstring**
13. 外部网络调用（LLM API、数据源 API）必须 mock；集成测试用 `unittest.mock`

## 每日分析流程（Daily Analysis Flow）

```
1. 数据同步     → trade-krono-cli sync-whitelist / scripts/full_history_sync.py
   + ETF 同步   → uv run python scripts/etf_history_sync.py（默认增量 / --full 全量 7 年）
2. 巴菲特筛选   → tests/buffett_screen_parallel.py（已过滤 ST / 北交所）
3. 三线共振     → scripts/multi_line_resonance.py（已过滤 ST / 北交所 / 港股；含 ETF 纯技术面）
4. 多头排列     → scripts/multi_scan.py（已过滤 ST / 北交所 / 港股；含 ETF 纯技术面）
5. 白名单分析   → scripts/daily_analysis.py（已过滤 ST / 北交所）
6. 双均线金叉   → scripts/double_ma_golden_cross.py（默认完整模式含 ROE+净利润；--lite 仅技术分析；
                  已过滤 ST / 北交所；ETF 以纯技术面参与，豁免 PE/ROE/净利润门槛）
7. AI 核实      → AGNES LLM（agnes-3.0-flash，10 RPM 限流）
8. 飞书推送     → scripts/feishu_cli.py（统一入口：buffett / text / daily / ci 模式）
9. 综合交集汇总 → scripts/combined_stock_analysis.py（多线共振交集 + 技术面 + 基本面 + AI + 飞书汇总 + 逐只）
10. MACD+均线筛选 → scripts/macd_ma_screener.py（MA5>MA13 + MACD DIF>0 + 剔ST/昨日涨停/市值<50亿/PE∈(0,30)；
                  结果作为第9步 "macd_ma" 来源线参与交集，已过滤 ST/北交所/港股/ETF）
11. 盘中扫描   → scripts/intraday_scan.py（盘中可选，分钟 K 线检测金叉/多头/MACD/三线共振，见「盘中扫描」节）
```

> **注意**：各筛选脚本均已内置 ST / 北交所（BSE）/ 港股过滤，逐只飞书推送逻辑已移除，
> 仅保留汇总推送 + AI 核实。ST / BSE 关键词与代码前缀统一维护在 `scripts/_utils.py`。

### ETF 数据支持（2026-10-21 起）

- **归一化**：ETF 代码（`51`/`58`→`sh.`，`159`/`156`/`158`→`sz.`）与普通股票统一前缀，
  由 `scripts/_utils.py::is_etf_ticker()` 识别；`get_provider_chain()` 对 ETF 路由 baostock 优先
  （baostock 原生支持 ETF K 线，实测 510300/159915/518880/588000/159949 全部成功）。
- **数据同步**：`scripts/etf_history_sync.py` 通过 akshare `fund_etf_category_ths` 发现全市场 ETF
  （约 1400+ 只），baostock→tonghuashun→mootdx 降级拉取；默认增量（补最新），`--full` 拉 2020 起全量
  （约 2 小时，建议每周一次）。K 线统一按 `adjustflag='1'` 入库（ETF 无复权概念，各 provider 返回一致）。
- **参与范围**：ETF 仅在**纯技术面**脚本中参与（三线共振 / 多头排列 / 双均线金叉的 MA+MACD 部分），
  无 PE/PB/ROE/净利润/现金流，基本面筛选（巴菲特六闸门、ROE/净利润门槛）自然豁免，
  结果带 `is_etf: true` 标记并在日志中单独统计。
- **共享数据导出**：`export_shared_data.py` 导出 CSV/Parquet 时 ETF ticker 与普通股票同表（sh.5xxxxx/sz.159xxx），
  下游 qlib/vnpy 可直接使用。

### 第 9 步：综合选股交集汇总（`scripts/combined_stock_analysis.py`）

将上述选股线当日结果中**共有的股票**（命中 ≥2 条线，可用 `--min-sources` 调整）做交集。
**巴菲特六闸门 + 白名单分析合并为「白名单/巴菲特」单一来源**（命中两路只计 1 条交集，避免计数虚高），
其余选股线各自独立计数。对每只交集候选：
  - 标注命中的选股线来源
  - 技术面评估（MA 排列 / 趋势 / 量能 / 综合分）
  - 基本面评估（PE_TTM / PB / 估值分 / 估值结论）
  - 按命中条数加权排序（共振越多越靠前）

随后 AI 核实 + 飞书推送：
  - **Kronos 预测**（可选，`--kronos 1`，推送前批量跑 Kronos K 线预测，写入每只卡片 + LLM 视图）
  - **历史回测**（可选，`--backtest 1`，逐只读取 research DB 近 `--backtest-days` 天信号做单股回测）
  - **汇总卡片**（`--push 1` 默认开启）
  - **逐只卡片**（`--per-stock-push 1` 默认开启，每只间隔 1s 防 webhook 限流）

数据来源为 `outputs/results/` 下当日各脚本产物：

| 选股线 | 来源键 | 结果文件 |
|------|------|------|
| 白名单/巴菲特 | `whitelist` | `buffett_dynamic_whitelist.txt` + `buffett_screen_YYYYMMDD.txt` + `outputs/results/<ticker>/` 目录（三路并集） |
| 三线共振 | `resonance` | `macd_resonance_YYYY-MM-DD.json` |
| 多头排列 | `multi_head` | `multi_head_YYYYMMDD.txt` |
| 双均线金叉 | `double_ma` | `double_ma_golden_cross_YYYY-MM-DD.json` |
| MACD+均线 | `macd_ma` | `macd_ma_screener_YYYYMMDD.json` |

```bash
uv run python scripts/combined_stock_analysis.py --date 2026-10-16       # 交集+分析+汇总推送+逐只推送
uv run python scripts/combined_stock_analysis.py --min-sources 3         # 仅 3 条线以上共振
uv run python scripts/combined_stock_analysis.py --push 0                # 跳过飞书推送
uv run python scripts/combined_stock_analysis.py --per-stock-push 0      # 跳过逐只推送
uv run python scripts/combined_stock_analysis.py --kronos 1 --backtest 1  # 卡片含 Kronos 预测 + 历史回测
```

### 盘中扫描（可选，`scripts/intraday_scan.py`）

盘中（9:30–15:00）实盘研判用，**分钟 K 线上检测「当日已触发」的技术信号**，与日级筛选（1–9 步）互补：
日级看趋势位置，盘中看「今天刚发生」的触发事件。

- **数据源**：分钟 K 线用 akshare `stock_zh_a_minute`（5/15/30/60min，股票 + ETF 通吃，约 13 个月），
  实时价用腾讯 `fetch_realtime_quote`（免费无 key）。
  > 注：mootdx 通达信行情服务器（7709/7719）在本网络环境不可达（`Quotes.bars()` 全空），故盘中 K 线改走 akshare 新浪接口。
- **信号定义**（15min 为主周期，判定取倒数第 2 根 `_REF_IDX=-2`，因最后一根未完成）：
  - `golden_cross`：MA5 刚上穿 MA13（前 MA5≤MA13 且 当前 MA5>MA13）
  - `multi_head`：close > MA5 > MA10 > MA20（多头排列）
  - `macd`：DIF>0 且 DIF 上行（MACD 12,26）
  - `resonance`：5min + 15min + 60min 三周期 MACD DIF 同时 >0（盘中三线共振变体）
- **扫描范围**：默认静态白名单 + 巴菲特动态白名单合并（约 500 只），已过滤 ST / 北交所 / 港股；
  `--tickers` 可显式指定（裸代码或带前缀均可，含 ETF）。
- **结果**：按命中信号数降序，输出 Rich 表 + JSON（`outputs/results/intraday_scan_*.json`），
  `--push 1` 飞书汇总推送。

```bash
uv run python scripts/intraday_scan.py                       # 白名单，全 4 类信号
uv run python scripts/intraday_scan.py --mode golden_cross   # 仅金叉
uv run python scripts/intraday_scan.py --mode resonance      # 盘中三线共振
uv run python scripts/intraday_scan.py --min-signals 2       # 至少命中 2 类
uv run python scripts/intraday_scan.py --tickers "600519,000858,510300" --push 1
```

### 一键全流程（`scripts/daily_flow.sh`）

```bash
bash scripts/daily_flow.sh              # 全流程（含飞书推送，不含数据导出）
NO_PUSH=1 bash scripts/daily_flow.sh    # 跳过所有飞书推送
NO_SYNC=1 bash scripts/daily_flow.sh    # 跳过第 1 步数据同步
SKIP_ETF=1 bash scripts/daily_flow.sh   # 跳过 ETF 同步
EXPORT=1 bash scripts/daily_flow.sh     # 额外执行共享数据导出
DRY_RUN=1 bash scripts/daily_flow.sh    # 只打印不执行
```

## 巴菲特六闸门筛选（Buffett Six-Gate Screening）
位置：`tests/buffett_screen_parallel.py`（并行版）或 `tests/buffett_screen.py`（串行版）
前置条件：`.env` 中配置 `HITHINK_FINANCE_API_KEY`（同花顺 Fuyao API）

### 闸门规则
| 闸门 | 指标 | 阈值 | 逻辑 |
|------|------|------|------|
| ① 便宜 | PE_TTM、PB | PE < 16 且 PB < 3 | 为盈利和净资产付的价格 |
| ② 好生意 | ROE、扣非ROE | ROE > 15% 且 扣非ROE > 12% | 护城河量化，扣非剔除一次性利润 |
| ③ 财务稳健 | 资产负债率 | < 50% | 厌恶高杠杆 |
| ④ 利润是真 | 经营现金流净额 | > 0 | 识别纸面利润 |
| ⑤ 能持续 | 3年净利CAGR | > 0 | 排除周期顶部一次性高盈利 |
| ⑥ 安全边际 | PE 10年历史分位 | < 30% | ⚠️ 个股无此接口，标注为未知 |

### 运行
```bash
# 并行版（推荐，~29分钟完成4966只）
uv run python tests/buffett_screen_parallel.py

# 串行版（约5+小时，不推荐）
uv run python tests/buffett_screen.py
```

### 历史记录
- 2026-08-31：全量筛选，38 只通过五闸门（①~⑤），详见 `outputs/results/buffett_screen_20260831.txt`
- 失败主因：① PE/PB 阈值（91.4%）、② ROE 不达标（7.0%）
- 闸门⑥ 因同花顺 API 不支持个股历史分位，无法验证

## 飞书推送服务（独立 CLI 工具）

位置：`scripts/feishu_cli.py`（调用核心逻辑 `scripts/feishu_core.py`）

### 使用方式
```bash
# 巴菲特筛选结果
uv run python scripts/feishu_cli.py buffett --result-file outputs/results/buffett_screen.txt

# CI 流水线结果
uv run python scripts/feishu_cli.py ci --status success --branch master --commit abc123 --jobs 'lint✅ test✅' --run-url "https://github.com/..."

# 每日投研分析
uv run python scripts/feishu_cli.py daily --status success --date 2026-09-04 --tickers "600519,000858" --top3 "600519:BUY" --run-url "https://github.com/..."

# 通用文本消息
uv run python scripts/feishu_cli.py text --content "📊 今日分析完成"

# 指定频道（配置文件中定义多个 webhook）
uv run python scripts/feishu_cli.py buffett --result-file outputs/results/buffett_screen.txt --channel alerts
```

### 配置
- 配置文件：`~/.config/feishu-notify/config.json`（权限 600）
- 模板：`~/.config/feishu-notify/config.json.example`
- 支持多频道（channels），每个频道可配置独立 webhook URL

### 跨项目调用
其他项目可通过以下方式调用：
```bash
# Shell 脚本中
uv run python /path/to/trade-krono-cli/scripts/feishu_cli.py buffett --result-file result.txt

# Python 代码中
from scripts.feishu_core import load_config, send_notification
config = load_config()
send_notification(mode="buffett", config=config, result_file="result.txt")
```

## 数据同步脚本（scripts/）

所有脚本使用同花顺 Fuyao API + baostock 主备降级，并发模型为 ThreadPoolExecutor。
数据库存储格式：pandas DataFrame 序列化为 pickle（`pd.to_pickle` / `pd.read_pickle(BytesIO)`）。

### 脚本清单
| 脚本 | 用途 |
|------|------|
| `full_history_sync.py` | 全量历史同步（2020-01-01 起，16 并发，~22 分钟完成 4893 只） |
| `retry_missing_history.py` | 重试缺失历史的股票（16 并发） |
| `fill_missing_and_today.py` | 补齐失败股票历史 + 同步今日增量（8 并发） |
| `check_data_integrity.py` | 数据完整性检查（重复/空数据/解码验证） |
| `cleanup_duplicates.py` | 清理同一 ticker 的多条记录（保留最长） |
| `full_sync_v2.py` | 优化版主备降级同步（沪深优先 tonghuashun） |
| `fill_missing_latest.py` | 补齐缺少最新日期的股票增量数据（tonghuashun 主，baostock 降级） |
| `etf_history_sync.py` | ETF K 线同步（akshare 同花顺发现全市场 ~1409 只，baostock 拉取；`--full` 全量 / `--codes` 指定） |
| `intraday_scan.py` | 盘中技术信号扫描（akshare 分钟 K 线 + 腾讯实时价；金叉/多头/MACD/三线共振，`--mode` 选类型，`--min-signals` 过滤，`--push` 飞书） |

### 数据质量指标（截至 2026-09-15）
- 总记录数：5,288（无重复）
- 非北交所：4,945 只
- 北交所：343 只
- 数据范围：2020-01-02 ~ 2026-09-14（5,288 只含最新数据，覆盖率 100%）
- DB 大小：约 590 MB
- 共享数据总大小：3.6 GB（7 种格式，供 7 个项目共用）

## 共享数据源机制（Shared Data）

`trade-krono-cli` 是唯一数据源。同步后通过 `export_shared_data.py` / `export_vnpy_data.py` 导出为 7 种格式，
各项目通过 symlink 直接读取，无需复制。

### 共享数据目录：`/run/media/onai/MyDisk/Work/shared_data/`

| 格式 | 路径 | 目标项目 |
|------|------|----------|
| CSV | `astock_daily_csv/` | Kronos / backtrader |
| Parquet | `astock_daily.parquet` | RD-Agent |
| HDF5 | `astock_daily.h5` | RD-Agent |
| TA CSV | `astock_daily_ta/` | TradingAgents-astock |
| qlib CSV | `astock_daily_qlib_csv/` | qlib dump_bin 源 |
| qlib Binary | `qlib_data/` | qlib / RD-Agent qlib |
| vnpy Parquet | `vnpy_daily/` | vnpy alpha |

### 各项目 Symlink

```
Kronos/data/astock_daily_csv          → /run/media/onai/MyDisk/Work/shared_data/astock_daily_csv
~/.tradingagents/cache                → /run/media/onai/MyDisk/Work/shared_data/astock_daily_ta
qlib/qlib/data/shared_data            → /run/media/onai/MyDisk/Work/shared_data/qlib_data
backtrader/datas/astock_daily_csv     → /run/media/onai/MyDisk/Work/shared_data/astock_daily_csv
vnpy/data/daily                       → /run/media/onai/MyDisk/Work/shared_data/vnpy_daily
~/.qlib/qlib_data/cn_data             → /run/media/onai/MyDisk/Work/shared_data/qlib_data
```

### 更新数据

```bash
# 全量/增量同步后，一键导出所有格式
bash scripts/post_sync_export.sh

# 或手动：
uv run python scripts/export_shared_data.py --format all --dest /run/media/onai/MyDisk/Work/shared_data
uv run python scripts/export_vnpy_data.py
```

## 代码风格（Code Style）
- Linter：ruff（行宽 100，豁免 RUF001/RUF002/RUF003、G004）
- Formatter：ruff format；导入排序：ruff 自动处理
- 字符串：f-strings，禁止 `.format()` 与 `%`；文件路径：`pathlib.Path`，禁止 `os.path.join()`
- 命名：函数/变量 `snake_case`，类 `PascalCase`，常量 `UPPER_SNAKE_CASE`
- 使用 `@dataclass` 表达内部状态，Pydantic `BaseModel` 处理外部输入边界（如 `PipelineConfig`）
- 依赖注入：协作者作为函数参数传入，不要在内部 `new`

## 架构约定（Architecture）
- **分层依赖**（由外向内）：`cli.py → cli_commands/ → pipeline/orchestrator.py → adapters/ / models/ → domain/ / data_providers/ → config.py`
- 领域层（`domain/`）禁止依赖外部项目或网络库
- 数据源统一返回标准化 `KlineData` / `RealtimeQuote` / `StockMetadata`；通过 `DataProvider` ABC 抽象
- 外部项目（TradingAgents / Kronos）通过 `adapters/` 层适配，不直接引用其内部 API
- 异常隔离：`ModuleError` 封装单模块失败，确保其他模块继续运行

## 测试约定（Testing）
- 测试文件：`tests/test_<module>.py`（扁平结构）；每个新模块必须有对应测试文件
- 共享 fixture：`make_mock_settings()`（在 `conftest.py` 中定义），禁止重复
- 多场景用 `@pytest.mark.parametrize`；测试函数命名描述场景（如 `test_get_user_token_when_expired_returns_none`）
- 单测试文件超过 500 行时，应按测试类/功能分组拆分为多个文件
- 禁止在单元测试中发起真实网络请求，必须 mock

## 安全约束（Security）
- 禁止硬编码任何凭证、API Key、Token
- 禁止提交 `.env`（`.env.example` 可提交）
- 日志禁止输出 PII；使用 `security.sanitize_for_log()` 脱敏

## 禁止事项（What NOT To Do）
- 禁止 `pip install` / `poetry` / `virtualenv`；禁止手动编辑 `uv.lock`；禁止 `from module import *`
- 禁止可变默认参数：`def f(x: list = [])` ❌，改用 `None` 哨兵；禁止裸 `except:`
- 禁止在库代码中使用 `print()` 调试
- 禁止在测试中破坏全局状态清理机制（跳过 `clear_all_globals()` 或直接读写 `config._settings`）
- 禁止在模块顶层直接实例化重型对象（`TASession()`、`KronosSession()`），使用懒加载或依赖注入
- 禁止在注释/docstring 中使用全角括号/逗号替代英文标点（ruff RUF001/002/003 已豁免中文项目正常写法）

## 提交与 PR
- 提交前必须跑通：`ruff check` + `mypy` + `pytest`
- Commit message 遵循 Conventional Commits（`feat:` / `fix:` / `chore:` 等）
- 一次逻辑变更一个 commit；跨模块任务拆分；新增依赖必须同 commit 更新 `uv.lock`

## 维护约定
- 本文件是"活文档"：团队切换工具时必须同 commit 更新
- 发现 AI 重复犯同一错误时，把对应禁令加入"禁止事项"

## Scripts 共享工具（scripts/_utils.py）

以下函数已从各脚本提取至 `scripts/_utils.py`，新脚本应从此处导入：

| 函数 | 用途 |
|------|------|
| `get_cache_db_path()` | 获取 pipeline_cache.db 路径（兼容测试隔离） |
| `ticker_to_thscode(ticker)` | sh.600519 → 600519.SH |
| `normalize_ticker_key(ticker)` | 统一键格式：`600519` → `sh.600519`，`000858` → `sz.000858`，`920001` → `bj.920001` |
| `get_all_tickers()` | 从缓存数据库读取所有不重复 ticker |
| `get_provider_chain(ticker)` | 根据股票类型返回 Provider 优先级链 |
| `check_data_freshness(logger_fn)` | 检查 K 线缓存是否已更新至预期日期 |
| `is_bse_code(code)` | 判断 6 位裸代码是否为北交所（920 开头） |
| `is_bse_or_hk(ticker)` | 判断带前缀 ticker 或裸代码是否为需排除的北交所/港股 |

共享常量（亦定义于此，各脚本统一引用，禁止在脚本内重复定义）：

| 常量 | 用途 |
|------|------|
| `ST_NAME_KEYWORDS` / `ST_NAME_KEYWORDS_MINIMAL` | ST 名称关键词（全市场扫描用完整版，白名单分析用精简版） |
| `BSE_CODE_PREFIXES` / `BSE_TICKER_PREFIXES` | 北交所代码/前缀 |
| `STOCK_NAMES` | 白名单常用股票的 6 位代码 → 名称映射（`daily_analysis` 会动态补充） |

**禁止**：在新脚本中重新定义上述函数与常量。

## 已知限制与架构备注

### DataProvider 配置
- 实际配置的 primary provider 是 `tonghuashun`（非 baostock），由 `get_data_factory()` 读取 Settings
- 北交所股票特殊处理：provider 链为 `["tonghuashun", "baostock"]`

### DecisionAdapter 文本解析
- `_keyword_fallback` 仅匹配英文关键词（BUY/SELL/HOLD/OVERWEIGHT/UNDERWEIGHT）
- 中文文本无法通过 keyword fallback 获得正确信号，需依赖 LLM JSON 输出

### EvalRecord 字段（`trade_krono_cli/eval_data.py`）
- 必要字段：`ticker, eval_date, horizon_days, pred_direction, pred_return_pct, actual_return_pct, actual_direction, is_direction_correct, error_pct`
- `from_dict` / `to_dict` 在 `trade_krono_cli/domain/evaluation.py` 中定义（非 `eval_data.py`）

### 缓存哈希
- `trade_krono_cli/cache/kline.py` 使用 SHA-256 with `b"TKC1"` 前缀
- 旧数据（pandas 序列化方式差异）哈希不匹配时仍尝试读取，仅记录 warning

### Agnes API 限流（三层，全局 RPM）

Agnes 免费档限制：**60s 内 ≤10 次请求**（RPM=10），相邻请求间隔 ≥1.5s。该限流分三层，
**全局**覆盖「本地 grok build（TUI）」与「本项目 CLI 脚本」两条调用路径，任一层失效都会触发 429。

| 层 | 位置 | 机制 | 状态 |
|---|---|---|---|
| ① Agnes 服务端 | `apihub.agnes-ai.cn` | 免费档 10 RPM 硬限流 → 429/503 | 服务端强制，无法绕过 |
| ② 本地 grok build | `~/.grok/rpm_proxy/rpm_proxy.py` | 本地限速反向代理（端口 8899，滑动窗口 10/60s+1.5s 间隔），grok 的 `[model.agnes-3-flash] base_url` 指向它 | 全局生效，见下方说明 |
| ③ CLI 脚本 | `trade_krono_cli/llm_rate_limiter.py` | `wait_for_rate_limit()` 线程安全滑动窗口；全仓所有 Agnes LLM 调用点必须挂接（清单见下） | 仅本项目脚本 |

**第 ③ 层挂接点清单（新增 Agnes LLM 调用时必须挂 `wait_for_rate_limit()`）**：

| 调用点 | 文件:行 | 用途 |
|---|---|---|
| `ai_verify._call_one` | `scripts/daily_analysis.py:250` | 白名单/共振/多头/第9步 AI 核实 |
| `run_ai_verification` | `tests/buffett_screening.py:1286` | 巴菲特筛选 AI 核实（单次调用） |
| `_verify_pe_percentile` | `tests/buffett_screening.py:101` | PE 历史分位 LLM 辅助判断（Gate ⑥） |

- 所有挂接点共用同一模块级滑动窗口（`_request_log`），跨脚本、跨线程并发也统一限 10 RPM
- `committee/__init__.py` 的 `llm_client.chat` 由 pipeline 调用方注入客户端，属离线批处理路径，未挂接
- `tests/buffett_enhanced_screen.py` 指向 deepseek（非 Agnes），无需挂接

**第 ② 层（全局 RPM 代理）要点**：
- 脚本：`~/.grok/rpm_proxy/rpm_proxy.py`（单文件标准库，端口 8899，`RPM_PROXY_PORT` 可覆盖）
- 配置：`~/.grok/config.toml` 中 `[model.agnes-3-flash] base_url = "http://127.0.0.1:8899/v1"`
  （原值 `https://apihub.agnes-ai.cn/v1`，改回该值即回滚直连）
- 守护：crontab `* * * * *` 每分钟检查，`pgrep -f rpm_proxy.py` 为空时自动拉起
- 日志：`~/.grok/rpm_proxy/proxy.log`（出现「限流等待 Ns 后放行」即窗口生效）
- 限流参数与第 ③ 层 `llm_rate_limiter.py` 对齐（60s≤10 次、间隔 1.5s），改限流需两处同步
- **回滚**：`sed -i 's|base_url = "http://127.0.0.1:8899/v1"|base_url = "https://apihub.agnes-ai.cn/v1"|' ~/.grok/config.toml`
  后重启 grok；代理可保留或 `pkill -f rpm_proxy.py` 终止
- 其他 grok 模型（`zhipu-glm4-flash` 等默认模型）**不**走此代理，仅 agnes-3-flash 路由生效

**排障提示**：
- grok 出现 429/401：先确认 `pgrep -f rpm_proxy.py`（代理活着？）→ 看 `proxy.log`（是否限流等待？）
  → `curl http://127.0.0.1:8899/v1/models`（代理连通？）→ 401 多为 shell 残留旧
  `AGNES_API_KEY` 环境变量（`_load_env` 的 `load_dotenv(override=False)` 不会覆盖已有 shell 变量，
  需 `unset AGNES_API_KEY` 或 source 新 key）。
- 401 `This token status is unavailable`：key 失效，更新 `.env`/`~/.grok/config.toml` 的 `api_key`。
