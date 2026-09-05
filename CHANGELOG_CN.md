# 更新日志

本项目的所有重要变更都将记录在此文件中。

英文版更新日志：[English Changelog](./CHANGELOG.md).

---

### v0.1.9 — 2026-09-05

**同步可靠性修复与定时任务调整：**

- **fix(config)**：修复双数据库问题——空字符串 `TRADING_KRONO_CACHE_DIR=""` 被 `Path("")` 解析为当前目录，导致缓存同时写入两个独立 SQLite 数据库；现在显式检查 `.strip()` 后再使用环境变量值
- **fix(sync)**：新增单只股票 30 秒超时保护（`_fetch_with_timeout` + `signal.alarm`）——防止 sync 在无响应数据源（mootdx/baostock）上无限卡死；超时股票记录日志并跳过
- **fix(sync)**：同步完成后自动导出由 `debug_insts=100` 改为 `debug_insts=0`——现导出全量数据集到 `daily_pv.parquet`，而非仅 100 只股票的 debug 子集
- **chore(cron)**：sync 定时任务从 10:00/16:00 调整为 10:00/15:30；RD-Agent pipeline 从 15:30 延后至 16:00，确保 sync 完成后才运行
- **test**：在 `test_sync_whitelist.py` 新增 2 项超时保护单元测试

---

### v0.1.8 — 2026-09-04

**Bug 修复、重构与测试扩展：**

- **fix(cache)**：修复 `cache.py` 中的 SQLite 连接泄漏——每次 DB 操作现在创建短生命周期连接并通过新增的 `_transaction()` / `_query_one()` / `_query_all()` 辅助方法自动关闭，防止长时间运行流水线时的 fd 耗尽
- **refactor(feishu)**：消除 scripts 层重复代码——将 7 个共享函数提取至 `scripts/feishu_utils.py`，删除 `feishu_core.py` 中无 def 头的残留死代码，删除 `feishu_notify.py` 中的重复函数体；净减少 260 行
- **refactor(external)**：将 557 行的单文件 `trade_krono_cli/external.py` 拆分为包 `trade_krono_cli/external/`，职责分离：`models.py`（数据类）、`git_ops.py`（git 操作）、`config_io.py`（YAML/lock 文件 I/O）；`__init__.py` re-export 所有公共 API，保持向后兼容导入
- **refactor(data)**：将 `next_business_days()` / `validate_data_freshness()` / `safe_float()` 从 `trade_krono_cli.data` 迁移至 `trade_krono_cli.utils.helpers`；data.py 通过 re-export 保持向后兼容
- **fix(pipeline)**：修复 `pipeline_core.py` 中 frozen dataclass 原地修改 bug（TAAnalysis 是冻结 dataclass，必须创建新实例而非原地修改字段）；`reporter.py` 新增 `html.escape()` XSS 防护；`merge.py` 将 `ret != ret` NaN 检查改为 `math.isnan()`；`risk/liquidity.py` 修正插值边界逻辑
- **test**：新增 246 项测试，覆盖 8 个文件（test_merge_edge_cases、test_orchestrator、test_akshare_provider、test_tushare_provider、test_version、test_backtest_engine、test_analytics_db、buffett_enhanced_screen）；测试总数：**2310 项通过**

---

### v0.1.7 — 2026-08-27

**GitHub Actions CI/CD 与测试覆盖扩展：**

- **新增 `.github/workflows/daily-run.yml`**：每日投研流水线，定时触发（UTC 07:30 = 北京时间 15:30，工作日）+ 手动触发（9 个可配置参数：股票代码、日期、置信度、信号过滤、全市场筛选、跳过 Kronos 等）
- **新增 `.github/workflows/ci.yml`**：CI 矩阵（lint / type-check / test），pytest 覆盖率报告上传 Codecov
- **新增 `scripts/_gh_summary.py`**：GitHub Actions 运行结果摘要脚本
- **测试覆盖扩展**：1411 → **2065 项**（+654 新增）：
  - `test_config_output.py` — `OutputConfig` 默认值、merge、roundtrip（6 项）
  - `test_config_abnormality.py` — `AbnormalityConfig` 默认值、校验、merge 边界（14 项）
  - `test_config_filters.py` — `FilterConfig` 全字段组合的 validate/merge（22 项）
  - `test_prediction_distribution.py` — `compute_single_sample`、`compute_multi_sample`、`build_distribution`、分位数逻辑（25 项）
  - `test_data_snapshot.py` — `DataSourceSnapshot`、`DataSnapshot`、`filter_kline_to_cut_date`（含冻结 dataclass、未来数据检测、copy 语义，20 项）
- 同步更新 README.md / README_CN.md 架构图中测试数量

---

### v0.1.6 — 2026-08-14

**Bug 修复与并发安全加固：**

- **修复 `KronosSession` 构造参数错误**：`KronosRunner` 不接受 `model_name`/`device` 参数，改为传递 `session=self`（`models/kronos_session.py`）
- **修复 `ta_runner.py` 变量作用域 Bug**：`_analyze_one_impl` 的 `finally` 块引用了外层方法定义的 `t0`，将其提升到 `_analyze_one_impl` 方法内部
- **修复 `trade_date` 缺失问题**：`build_config` 返回值不含 `trade_date`，在调用适配器前显式注入
- **修复 `HorizonMetrics` 缺少动态属性**：为 `eval_data.py` 添加 `rank_ic_*`、`ic_*`、`sortino_ratio`、`calmar_ratio`、`turnover`、`alpha_vs_benchmark` 等字段的默认值 0.0
- **修复 `EvaluationSummary` 缺少 IC 聚合字段**：新增 `ic_composite_rank_mean`、`ic_kronos_rank_mean`、`alpha_best_benchmark` 等字段，并在 `_compute_summary` 中调用 `compute_ic_metrics`
- **修复 `FailureStore` 多线程竞态**：添加 `threading.Lock` 保护 `record()` 和 `_save()` 操作
- **扩展 Session 缓存 key**：`KronosSession` 纳入 `T`/`top_p`/`lookback`，`TASession` 纳入 `max_debate_rounds`/`output_language`
- **修复 SELL 信号 `expected_return` 符号反转**：移除 `-pct` 的负号操作
- **修复 `run` 命令 `--config` 参数冲突**：股票列表文件参数改为 `--stock-file`/`-f`，Pipeline 配置文件保持 `--config`/`-c`
- **修复 `kronos` 命令缺少 `ta_cache_fallback` 参数**：补齐降级策略 CLI 接口
- **SQLite 连接添加 timeout**：`cache.py` 中 `connect()` 增加 `timeout=10.0`
- **测试数量**：**1164 项通过**（从 1106 增长），0 失败

---

### v0.1.5 — 2026-08-14

**领域层重构与代码质量提升：**

- **新增 `domain/` 领域层**：`SignalAssessment`、`InvestmentDecision`、`Experiment`、`Evaluation`、`PredictionDistribution`、`RiskAssessment` 均为纯领域对象（无 I/O，无 SQLite 耦合）
- **消除重复代码**：移除重复的 `SignalConflict` 枚举、`_compute_ev` 函数、`ExperimentType`/`Hypothesis` 类，统一从 `domain/` 导入
- **清理 `kronos_runner.py`**：移除重复的 `prediction_distribution` 字段，统一通过 `prediction_uncertainty` 模块处理不确定性
- **删除 `research_db.py` 中未使用的方法**：移除未被调用的 `insert_signal_assessment`、`insert_decision_domain`、`insert_experiment_domain`（减少约 103 行）
- **新增流水线资源管理**：`resource_manager.py` 和 `resource_pool.py`，管理 LLM 客户端和 GPU 会话生命周期
- **新增 `signal_lifecycle.py`**：追踪信号跨次运行的状态演变
- **新增 `experiment_registry.py`**：基于假设的实验追踪，支持通过/失败评估
- **新增 `committee.py`**：LLM 驱动的投资委员会审议存根
- **新增分析与评估模块**：`analytics_db.py`、`artifact_manifest.py`、`eval_ic.py`、`eval_benchmark.py`、`eval_walkforward.py`
- **测试数量**：**1106 项通过**（从 468 增长）

---

### v0.1.4 — 2026-08-13

**优雅降级机制：**

- 三种降级模式：`strict`（默认）、`ta_only_on_kronos_fail`、`ta_cache_fallback`
- Kronos 不可用时，TA-only 结果仍保留在报告中（标记为 `⚠ TA-only`）
- TA 分析失败时，可回退到研究数据库中最近一次成功的缓存 TA 结果（标记为 `📦 缓存TA`）
- 新增 CLI 参数：`--degrade-mode` 和 `--ta-cache-fallback`
- 新增环境变量：`DEGRADE_MODE`、`TA_CACHE_FALLBACK_ENABLED`、`TA_CACHE_MAX_AGE_DAYS`
- 配置校验器在 `TA_CACHE_FALLBACK_ENABLED=true` 但 `DEGRADE_MODE` 不匹配时发出语义警告
- 提取了 orchestrator 中的 `_apply_ta_cache_fallback()`、reporter 中的 `_degradation_badge()`、cli_commands 中的 `_build_degrade_overrides()` 辅助函数
- 测试数量：**468**（从 459 增长）；新增 `tests/test_degradation.py`，共 33 项测试

---

### v0.1.3 — 2026-08-13

**配置集中校验与分层管理：**

- 新增 `configs/schema.py` — 所有打分权重、风险维度权重、分段映射阈值（波动率 0%→0 / 60%→100、回撤断点、流动性 log 阈值、市场环境动量阈值）统一在此定义，均为不可变 dataclass，支持 `validate()` 和 `merge()`
- `RiskEngine` 改为接受 `RiskConfig`；所有 `calc_*_risk()` 函数增加可选 `thresholds` 参数
- `default_scorer()` / `merge_results()` 增加可选 `ScoringConfig` / `RiskConfig` 参数
- `PipelineConfig` 携带 `scoring: ScoringConfig` 和 `risk: RiskConfig` 字段；`from_dict()` 正确还原嵌套 dataclass
- `cli_commands._load_env()` 在启动时调用 `run_validation()` — 致命错误直接退出，警告降级输出
- `config_validator.validate_settings()` 返回 `(errors, warnings)` 元组
- **优先级文档化**：CLI 参数 > 环境变量/.env > PipelineConfig YAML > Schema 默认值
- YAML 序列化修复：改用 `yaml.safe_dump`（不再产生 `!!python/tuple` 标签）；`to_dict()` 递归转换 tuple→list
- 测试数量：**468**（从 459 增长）；新增所有风险模块自定义阈值测试、`test_from_dict_restores_dataclasses`、`test_merge_works_with_loaded_config`

---

### v0.1.2 — 2026-08-12

**流水线收敛重构：**

- 删除冗余的根目录 `merge.py`、`report.py`、`pipeline.py` 及 `pipeline/scorer.py`
- 将合并/打分/报告逻辑统一收敛到 `pipeline/merge.py` 和 `pipeline/reporter.py`
- `pipeline/__init__.py` 现只导出 `QuantPipeline` 和 `PipelineFactory`，子模块内部直接导入
- 移除未使用的 `MergedItem` dataclass、生产代码中从未调用的 `score_merged_results`，以及 `orchestrator.py` 中的 4 个无效导入
- `filter_pool` 现直接返回 `list[StockAnalysisResult]`，不再通过 dict 包装泄露内部结构
- 测试数量：**459**（从 398 增长）；覆盖率保持 **87%**

---

### v0.1.1 — 2026-08-12

**代码质量与静态分析：**

- **mypy 零错误**：38 个源文件全部通过类型检查；修复了 `kronos_runner.py`、`ta_runner.py`、`batch_runner.py`、`cache.py`、`errors.py`、`external.py`、`trading_constraints.py`、`prediction_eval.py`、`pipeline/orchestrator.py`、`logging_config.py` 中的类型注解问题
- **覆盖率 87%**（从 80% 提升）：`prediction_eval.py` 94%、`ta_runner.py` 91%、`kronos_runner.py` 83%、`cli.py` 56%
- 测试数量：**459**（从 398 增长）；新增 CLI 入口、kronos_runner 设备解析、ta_runner 配置/校验/图、prediction_eval 边界情况、batch runner、config_validator、health 检查、merge 边界/不确定性用例、流水线收敛重构

**新包结构（模块化重构）：**

- `pipeline/` — orchestrator（ThreadPoolExecutor + raw 报告自动保存）、data_fetcher（并行 K 线获取）、merge（打分 + 风险惩罚 + 排名）、reporter（JSON/HTML/控制台）
- `models/` — kronos_session（懒加载、设备选择）、ta_session（供应商/辩论状态）
- `batch/` — batch_runner（异步信号量控制批量预测）

**新增测试文件：** `test_cli.py`、`test_kronos_runner.py`、`test_ta_runner.py`、`test_prediction_eval.py`、`test_batch_runner.py`、`test_config_validator.py`、`test_health.py`、`test_merge_edge_cases.py`、`test_merge_uncertainty.py`、`integration/test_pipeline_integration.py`、`test_pipeline_config.py`

---

### v0.1.0 — 2026-08-12

**代码质量与安全修复：**

- 在 `security.py` 新增 `sanitize_for_log()` — 统一的 API key 脱敏工具函数，替换 `kronos_runner.py` 和 `ta_runner.py` 中原有的重复内联正则
- 在 `security.py` 新增 `ensure_import_path()` — 统一 harness 优先的 sys.path 注入逻辑，消除两个 runner 模块中的重复代码
- 修复 `ta_runner.py` 中 `_TRAIDINGAGENTS_IMPORTED` 拼写错误 → `_TRADINGAGENTS_IMPORTED`
- 将魔法截断字面量（`[:500]`、`[:300]`）提取为模块级常量，分布于 `ta_runner.py`、`pipeline/merge.py`、`ta_decision.py`、`cache.py`
- 重构 `EvaluationSummary` — 用按 horizon 分组的 `HorizonMetrics` dataclass 替代原有 30+ 个平铺字段；同步更新所有调用方
- 修复 `cache.py` 中 SQL f-string 表名插值问题 — 新增 `_validate_table_name()` 白名单校验助手
- 移除 `HorizonMetrics` 上重复的 `@dataclass` 装饰器

**测试：** `test_security.py` 新增 4 个，`test_prediction_eval.py` 新增 2 个，总计 162 个测试全部通过。
