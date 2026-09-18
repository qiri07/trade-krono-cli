# 代码质量检查报告

**日期**: 2026-09-18  
**检查范围**: trade-krono-cli（主要）、RD-Agent-Work（次要）  
**对照标准**: AGENTS.md（第140-320行约束规则）

---

## 总体状态

| 项目 | 状态 |
|------|------|
| trade-krono-cli | **NEEDS_WORK** |
| RD-Agent-Work | **PASS** |

---

## 一、trade-krono-cli 检查结果

### 1. Lint 结果（ruff check）

**状态**: ⚠️ 3 个错误（全部可自动修复）

| 规则 | 文件 | 行号 | 说明 |
|------|------|------|------|
| I001 | `backtest_kronos.py` | :8 | `from __future__ import` 括号格式不正确 |
| I001 | `scripts/buffett_daily_analysis.py` | :21-23 | 导入块未排序 |
| I001 | `tests/test_daily_analysis.py` | :10-17 | 导入块未排序 |
| F401 | `scripts/buffett_daily_analysis.py` | :23 | 未使用导入 `_get_stock_name` |
| F401 | `scripts/incremental_sync_latest.py` | :23 | 未使用导入 `_load_env` |

**修复命令**: `uv run ruff check . --fix && uv run ruff format .`

---

### 2. 类型检查（mypy）

**状态**: ✅ PASS  
**结果**: `Success: no issues found in 215 source files`

---

### 3. 测试（pytest）

**状态**: ⚠️ 4 个失败，2785 个通过，3 个跳过

| 失败测试 | 文件:行号 | 原因 |
|----------|-----------|------|
| `TestConstants.test_whitelist_default` | `tests/test_daily_analysis.py:229` | `.env` 中 `DAILY_ANALYSIS_WHITELIST` 已扩展为21只股票，测试仍期望旧默认值5只 |
| `test_predict_batch_sends_single_batch_when_within_limit` | `tests/test_kronos_runner_inference.py:295` | mock_adapter.predict 未被调用（预测路径变更导致断言失败） |
| `test_predict_batch_splits_into_multiple_batches` | `tests/test_kronos_runner_inference.py:333` | 同上，mock 调用计数为0而非2 |
| `test_predict_batch_pads_shorter_series` | `tests/test_kronos_runner_inference.py:374` | `call_args` 为 None，mock 绑定方式失效 |

**修复建议**:
1. `test_whitelist_default`: 更新测试断言以匹配当前 `.env` 中的配置值，或在 `conftest.py` 中 mock 该配置
2. `test_kronos_runner_inference` 三个测试: 检查 `predictor.py` 的 `predict_batch` 方法最新实现，更新 mock 断言方式

---

### 4. Git 未提交变更

**11 个文件有变更**:

```
.gitignore                                    |   45 -
AGENTS.md                                     |   20 +-
pyproject.toml                                |    1 +
scripts/daily_analysis.py                     |  251 ++-
scripts/export_shared_data.py                 |    7 +-
scripts/post_sync_export.sh                   |   10 +-
tests/test_daily_analysis.py                  |    4 +-
trade_krono_cli/config.py                     |   10 +
trade_krono_cli/kronos_predictor/predictor.py |   16 +-
trade_krono_cli/kronos_runner.py              |    2 +-
uv.lock                                       | 2332 +++++++++++++++----------
```

---

### 5. AGENTS.md 规则合规性详细检查

#### ✅ 已合规的项目

| 规则 | 检查结果 |
|------|----------|
| 所有函数签名有类型注解 | ✅ 188/188 库文件均有 `->` 返回类型注解 |
| 模块顶部 `from __future__ import annotations` | ✅ 188/188 库文件已添加 |
| 现代类型语法 (`X \| None`, `list[str]`) | ✅ 未发现旧式 `Optional[]` / `List[]` 用法 |
| 禁止 `os.path.join()` | ✅ 项目代码中无 `os.path.join` 调用 |
| 字符串用 f-strings | ✅ 未发现 `.format()` 或 `%` 格式化 |
| 库代码禁止 `print()` | ✅ `trade_krono_cli/` 中无裸 `print()`（仅有 `console.print()` 用于 CLI 输出） |
| 禁止可变默认参数 | ✅ 未发现 `def f(x=list=[])` 模式 |
| 禁止裸 `except:` | ✅ 未发现 |
| 禁止 `except Exception: pass` | ✅ 未发现 |
| 密钥从 `.env` 读取 | ✅ 未发现硬编码 API Key |
| 测试用 pytest | ✅ 全部使用 pytest，无 `unittest.TestCase` |

#### ⚠️ 部分合规但需注意

| 规则 | 检查结果 |
|------|----------|
| 禁止 `Any`（需注释说明） | ⚠️ `trade_krono_cli/adapters/base.py` 及 `batch_runner.py` 中有 `Any` 使用，属于合理场景但缺少注释说明 |
| 禁止 `# type: ignore`（需附错误码与理由） | ⚠️ `scripts/daily_analysis.py:31` 的 `# type: ignore[import-not-found]` 和 `scripts/daily_analysis.py:67` 的 `# type: ignore[misc,assignment]` 理由不充分 |
| `except Exception as e:` 日志上下文 | ⚠️ 发现 20+ 处使用，部分异常日志缺少 ticker/日期等关键上下文 |
| scripts 目录规范 | ⚠️ `scripts/__init__.py` 缺少 `from __future__ import annotations` |

---

## 二、RD-Agent-Work 检查结果

### 1. 测试（pytest）

**状态**: ✅ PASS  
**结果**: 679 passed in 30.78s

### 2. 代码风格抽查

| 检查项 | 结果 |
|--------|------|
| `print()` 在脚本中 | ℹ️ `analyze_factors.py` 等多处使用（脚本类代码允许） |
| `os.path.join` | ℹ️ `RD-Agent/rdagent/utils/env.py` 等多处使用（独立子项目，不受 trade-krono-cli AGENTS.md 约束） |
| Python 版本 | Python 3.12.13（.venv），系统 Python 3.14.4 |

> **注意**: RD-Agent-Work 是其独立项目，有自己的架构和约束。AGENTS.md 约束仅适用于 trade-krono-cli 项目本身。

---

## 三、优先级修复清单

### 🔴 P0 - 测试失败（阻塞发布）

1. **`tests/test_daily_analysis.py::test_whitelist_default`**  
   - 原因: `.env` 中 `DAILY_ANALYSIS_WHITELIST` 已扩展，测试断言未同步  
   - 修复: 更新测试期望值，或在 conftest 中强制 mock 环境变量

2. **`tests/test_kronos_runner_inference.py` 中 3 个测试失败**  
   - 原因: `predictor.py` 的 `predict_batch` 方法实现变更后，测试 mock 断言方式失效  
   - 修复: 更新 mock 断言以匹配新的预测执行路径

### 🟡 P1 - Lint 问题（影响代码整洁）

3. `backtest_kronos.py:8` — `from __future__ import (...)` 括号格式
4. `scripts/buffett_daily_analysis.py:23` — 移除未使用的 `_get_stock_name` 导入
5. `scripts/incremental_sync_latest.py:23` — 移除未使用的 `_load_env` 导入
6. `tests/test_daily_analysis.py:10-17` — 导入排序

> 执行 `uv run ruff check . --fix && uv run ruff format .` 可自动修复全部 lint 问题

### 🟢 P2 - 代码规范改进

7. `scripts/__init__.py` — 缺少 `from __future__ import annotations`
8. `scripts/daily_analysis.py:31,67` — `# type: ignore` 缺少充分理由说明
9. `trade_krono_cli/adapters/` 中的 `Any` — 建议添加注释说明使用原因
10. `except Exception as e:` 日志上下文 — 部分异常日志缺少 ticker/日期等关键上下文

---

## 四、总结

trade-krono-cli 整体代码质量较高：
- **mypy 零错误**（215 个源文件全部类型安全）
- **2785/2789 测试通过**（仅 4 个失败，均为近期变更导致的测试滞后）
- **AGENTS.md 核心规则全面合规**（无 `print()`、无 `os.path.join`、无旧式类型语法、无裸 except）

主要问题集中在：
1. 近期代码变更后测试未及时同步（4 个失败）
2. 少量 lint 格式问题（3 个错误，可自动修复）
3. 少数 `# type: ignore` 和 `Any` 使用缺少注释说明

**RD-Agent-Work** 项目测试全部通过（679/679），代码风格为独立项目规范，不受 trade-krono-cli AGENTS.md 约束。
