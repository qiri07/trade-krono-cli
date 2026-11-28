# AGENTS.md — trade-krono-cli 项目说明

> AI 编程助理会话开始时自动加载。所有生成/修改的代码必须遵守本文件。

## 项目概览
- **用途**：A 股投研 + 预测流水线，融合多源分析输出推荐报告
- **语言**：Python 3.10+
- **包管理**：`uv`（禁止 `pip` / `poetry` / `virtualenv`）
- **测试**：pytest（全绿门槛）
- **数据存储**：SQLite 缓存、JSON/HTML 报告、日志

## 常用命令
```bash
uv sync                                 # 安装全部依赖（含 dev）
uv add <pkg>                            # 添加运行时依赖
uv add --dev <pkg>                      # 添加开发依赖
uv run trade-krono-cli run --tickers "600519,000858" --date 2026-08-11  # 一键运行
uv run trade-krono-cli ta --tickers "600519"                            # 仅分析
uv run trade-krono-cli kronos --tickers "600519"                        # 仅预测
uv run trade-krono-cli sync-whitelist                          # 仅同步白名单股票
uv run trade-krono-cli a-stock-screen --all                    # A股硬过滤（全部缓存股票）
uv run python scripts/launch_signal_scan.py                    # 大行情启动信号扫描
uv run python scripts/intraday_scan.py                         # 盘中技术信号扫描
uv run python -m scripts.six_dim_screen                        # 六维选股
uv run python scripts/combined_stock_analysis.py               # 综合交集汇总
uv run python scripts/buffett_daily.py                         # 巴菲特筛选+推送
uv run python scripts/full_history_sync.py                     # 全量历史同步
uv run python scripts/etf_history_sync.py                      # ETF 数据同步
uv run python scripts/fetch_reference_data.py                  # 股票参数刷新
uv run python scripts/double_ma_golden_cross.py                # 双均线金叉筛选
uv run python scripts/check_data_integrity.py                  # 数据完整性检查
uv run python scripts/backup_cache.py                          # 数据库备份
uv run ruff check .                    # 代码检查
uv run ruff check --fix .              # 代码检查 + 自动修复
uv run mypy .                          # 类型检查
uv run pytest                          # 运行全部测试
uv run pytest -x                       # 首个失败即停
```

提交前必须全绿：`uv run ruff check . && uv run mypy . && uv run pytest`

## 项目结构（简要）
```
trade_krono_cli/    # 核心代码：CLI、流水线、数据提供、领域模型、数据库等
scripts/            # 运维脚本：数据同步、选股扫描、推送通知、共享数据导出等
tests/              # 自动化测试
outputs/            # 运行结果：缓存、报告、日志（部分已 gitignore）
```

## 核心流程
1. **数据同步**：同步股票/ETF/港股行情与基本面元数据
2. **股票池过滤**：剔除 ST / 北交所 / 港股 / ETF / 停牌 / 低价 / 次新 / 低流动性 / 小市值
3. **多维选股**：巴菲特筛选、三线共振、多头排列、双均线金叉、启动信号、MACD+均线等
4. **交集汇总**：多选股结果取交集，输出推荐名单，支持 AI 核实与推送
5. **盘中扫描**：盘中分钟级技术信号检测（互补日级筛选）
6. **共享数据导出**：将缓存数据导出为多种格式，供下游项目直接使用

## 关键规则
1. 所有命令使用 `uv run` 前缀，禁止裸 `python` / `pytest`
2. 依赖仅通过 `uv add` 添加，禁止手动编辑依赖文件，禁止创建 `requirements.txt`
3. 所有函数签名须有类型注解；模块顶部加 `from __future__ import annotations`
4. 使用现代类型语法：`X | None`、`list[str]`；禁止 `Any`（除非附注释说明）
5. 测试一律用 pytest，禁止 `unittest.TestCase`
6. 库代码禁止 `print()`，必须用 `loguru.logger`
7. 捕获具体异常，禁止 `except Exception: pass`；禁止可变默认参数
8. 禁止硬编码任何凭证、API Key、Token；禁止提交 `.env`
9. 日志不得输出密钥或个人敏感信息；统一使用 `setup_logger()` 初始化
10. 外部网络调用（LLM API、数据源 API）在测试中必须模拟
11. 测试/试跑中禁止触发真实飞书推送，必须带 `--no-push`（或 `--push 0`）

## 主要模块说明
- **启动信号扫描**（`scripts/launch_signal_scan.py`）：全市场扫描大行情启动特征股票，支持单轮 / 自动调参 / 参数网格搜索，结果汇总为一条推送消息
- **盘中扫描**（`scripts/intraday_scan.py`）：盘中分钟级技术信号检测（金叉 / 多头 / MACD / 三线共振）
- **六维选股**（`scripts/six_dim_screen.py`）：逻辑 / 估值 / 位置 / 趋势 / 股东人数 / 量价六维打分
- **综合交集汇总**（`scripts/combined_stock_analysis.py`）：多选股结果取交集，生成推荐报告并推送
- **巴菲特筛选**（`tests/buffett_screen_parallel.py`）：基本面六闸门选股
- **飞书推送**（`scripts/feishu_cli.py`）：统一通知入口，支持多种消息模板与多频道
- **共享数据导出**（`scripts/export_shared_data.py`）：将缓存数据导出为 CSV / Parquet / HDF5 / qlib / vnpy 等多种格式

## 提交要求
- 提交前必须运行：代码检查 + 类型检查 + 测试，全部通过
- Commit message 遵循 Conventional Commits（`feat:` / `fix:` / `chore:` 等）
- 一次逻辑变更一个 commit；跨模块任务拆分；新增依赖必须同 commit 更新锁文件
- 本文件是"活文档"：团队切换工具时须同 commit 更新
