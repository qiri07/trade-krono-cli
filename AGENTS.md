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
uv run python tests/buffett_screen_parallel.py         # 巴菲特六闸门全量筛选（并行版）
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
├── cli_commands/         # CLI 命令包（向后兼容：cli_commands.py 为薄包装）
│   ├── __init__.py       # 统一导出 run/ta/kronos/repo_* 等
│   ├── core.py           # 共享工具函数 + run/ta/kronos 主流程命令
│   ├── repo.py           # repo status/doctor/update/pin 子命令
│   ├── maintenance.py    # 向后兼容薄包装（re-export 子模块）
│   ├── maintenance_status.py   # status 命令
│   ├── maintenance_cache.py    # clear-cache / warm-cache 命令
│   ├── maintenance_sync.py     # sync-universe / sync-whitelist + _resolve_tickers
│   ├── maintenance_history.py  # history 命令
│   ├── maintenance_eval.py     # eval-prediction 命令
│   └── maintenance_retry.py    # retry-failed 命令
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
tests/buffett_screen_parallel.py   # 巴菲特六闸门筛选器（并行版，20 并发）
tests/check_cache_integrity.py     # 缓存完整性检查工具
tests/check_cache_quality.py       # 缓存质量分析工具
tests/test_sync_whitelist.py       # sync-whitelist / sync-universe 单元测试
tests/conftest.py                  # 共享 fixture（make_mock_settings）+ pytest_configure/env var 路由 + clear_all_globals hook
tests/test_*.py                    # 扁平化测试结构：每个源模块对应一个测试文件（91 个测试文件）
outputs/                     # 运行时产物（gitignore）
outputs/results/             # 报告输出目录（gitignore 中 *.db/*.log，结果文件可提交）
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
