"""
TradingAgents-Astock 封装层（业务逻辑层）。

职责边界：
  · 股票分析调度（analyze_one / analyze_batch）
  · 报告提取与截断（_extract_reports）
  · 决策解析（_extract_decision → InvestmentDecision）
  · 缓存读写
  · 结果落盘（save_results / save_raw_reports）

资源管理（LLM 密钥校验 / 适配器懒加载 / graph 初始化）由 TASession 负责。
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Callable, Any

from loguru import logger

from trade_krono_cli.config import get_settings, Settings
from trade_krono_cli.security import (
    validate_ticker,
    validate_date,
    retry,
    sanitize_for_log,
)
from trade_krono_cli.retry_policy import (
    smart_retry,
    RetryPolicy,
    classify_error,
    get_failure_store,
)
from trade_krono_cli.cache import get_cache
from trade_krono_cli.version import compute_config_hash, get_ta_prompt_version
from trade_krono_cli.ta_decision import InvestmentDecision, Signal, DecisionAdapter
from trade_krono_cli.errors import ModelLoadError, TradeKronoError
from trade_krono_cli.adapters import TradingAgentsAdapterImpl


# 摘要截断长度
SUMMARY_TRUNCATE_LEN = 500

# 懒加载：首次调用时才 import，避免无密钥时直接报错
_TRADINGAGENTS_IMPORTED = False


def _ensure_tradingagents_import(settings) -> None:
    """将 TradingAgents-astock/agent-harness 加入 sys.path 并导入核心模块。
    （已迁移至 adapters 层；此函数保留供旧测试兼容。）
    """
    global _TRADINGAGENTS_IMPORTED
    if _TRADINGAGENTS_IMPORTED:
        return
    from trade_krono_cli.security import ensure_import_path
    harness_root = settings.tradingagents_root / "agent-harness"
    ta_root = settings.tradingagents_root
    ensure_import_path(harness_root, ta_root)
    _TRADINGAGENTS_IMPORTED = True
    logger.debug(f"TradingAgents-astock 路径已加入: {harness_root} + {ta_root}")


def clear_tradingagents_imported() -> None:
    """重置 TradingAgents 懒加载状态，用于测试隔离。"""
    global _TRADINGAGENTS_IMPORTED
    _TRADINGAGENTS_IMPORTED = False


_REPORT_KEYS = [
    "market_report",
    "sentiment_report",
    "news_report",
    "fundamentals_report",
    "policy_report",
    "hot_money_report",
    "lockup_report",
]
_REPORT_ALIAS = {
    "market_report": "market",
    "sentiment_report": "sentiment",
    "news_report": "news",
    "fundamentals_report": "fundamentals",
    "policy_report": "policy",
    "hot_money_report": "hot_money",
    "lockup_report": "lockup",
}


@dataclass
class StockAnalysisResult:
    """单只股票的分析结果。"""
    ticker: str
    date: str
    signal: Optional[str] = None
    confidence: Optional[float] = None
    position_size: Optional[float] = None
    reasoning: Optional[str] = None
    reports: dict[str, str] = field(default_factory=dict)
    # 完整原始报告（永不截断，用于 RAG / 回测 / Agent memory）
    reports_raw: dict[str, str] = field(default_factory=dict)
    risk_assessment: Optional[str] = None
    decision_raw: Optional[dict] = None
    error: Optional[str] = None
    elapsed_sec: float = 0.0

    # 新增：标准化投资决断（由 DecisionAdapter 解析生成）
    investment_decision: Optional[InvestmentDecision] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        if self.investment_decision is not None:
            d["investment_decision"] = self.investment_decision.to_dict()
        return d

    @property
    def decision(self) -> InvestmentDecision:
        """便捷访问：优先返回结构化 decision，fallback 到 legacy 字段。"""
        if self.investment_decision is not None:
            return self.investment_decision
        # fallback：从 legacy 字段构造
        sig = Signal(self.signal) if self.signal else Signal.HOLD
        return InvestmentDecision(
            signal=sig,
            confidence=self.confidence or 50.0,
            thesis=self.reasoning or "",
        )

    def is_buy(self, min_confidence: float = 55.0) -> bool:
        d = self.decision
        return (
            d.signal in (Signal.BUY,)
            and d.confidence >= min_confidence
            and self.error is None
        )


class TradingAgentsRunner:
    """
    生产级 TradingAgents 封装（业务逻辑层）。

    特点：
    - 批量分析（失败隔离、进度回调）
    - checkpoint 自动管理
    - 结构化输出（StockAnalysisResult）
    - 缓存支持

    资源管理（LLM 密钥校验 / 适配器懒加载）由 TASession 负责。
    """

    def __init__(
        self,
        session: Optional[Any] = None,
        llm_provider: Optional[str] = None,
        deep_think_llm: Optional[str] = None,
        quick_think_llm: Optional[str] = None,
        backend_url: Optional[str] = None,
        max_debate_rounds: Optional[int] = None,
        checkpoint_enabled: Optional[bool] = None,
        output_language: str = "Chinese",
        safe_mode: bool = True,
        no_cache: bool = False,
        settings: Optional[Settings] = None,
        retry_policy: Optional[RetryPolicy] = None,
    ):
        self._session = session
        self._settings = settings or get_settings()
        self._cache = None if no_cache else get_cache()

        # 配置合并：显式参数 > settings 默认值
        self.llm_provider = llm_provider or self._settings.llm_provider
        self.deep_think_llm = deep_think_llm or self._settings.deep_think_llm
        self.quick_think_llm = quick_think_llm or self._settings.quick_think_llm
        self.backend_url = backend_url or self._settings.backend_url
        self.max_debate_rounds = (
            max_debate_rounds
            if max_debate_rounds is not None
            else self._settings.max_debate_rounds
        )
        self.checkpoint_enabled = (
            checkpoint_enabled
            if checkpoint_enabled is not None
            else self._settings.checkpoint_enabled
        )
        self.output_language = output_language

        if safe_mode and self._session is None:
            self._validate_provider()

        # 重试策略：CLI 参数 > Settings 默认
        self._retry_policy = retry_policy or RetryPolicy(
            max_attempts=get_settings().retry_max_attempts,
            base_delay=get_settings().retry_base_delay,
            jitter=get_settings().retry_jitter,
            rate_limit_backoff=get_settings().retry_rate_limit_backoff,
            rate_limit_max_wait=get_settings().retry_rate_limit_max_wait,
        )

        logger.info(
            f"🤖 TradingAgentsRunner 就绪 | provider={self.llm_provider} "
            f"deep={self.deep_think_llm}"
        )

    # ── 资源访问（委托给 session）────────────────────────────────────────────

    @property
    def adapter(self) -> Any:
        """返回底层 adapter 实例（供测试注入或外部访问）。"""
        if self._session is not None:
            return self._session.adapter
        # 无 session 时直接创建（向后兼容）
        return self._make_adapter()

    def _make_adapter(self) -> Any:
        """创建新的 adapter 实例（供 session 或测试使用）。"""
        adapter = TradingAgentsAdapterImpl()
        adapter.load(self._settings)
        return adapter

    # ── 资源管理（仅在没有 session 时生效）──────────────────────────────────

    def _validate_provider(self) -> None:
        """检查 LLM 密钥是否可用（仅在无 session 时调用）。"""
        from trade_krono_cli.security import KeyVault
        vault = KeyVault()
        available = vault.available_providers()
        if not available:
            raise RuntimeError(
                "❌ 未检测到任何 LLM API 密钥。请在 .env 中设置 "
                "DEEPSEEK_API_KEY / OPENAI_API_KEY / ANTHROPIC_API_KEY 之一"
            )
        if self.llm_provider not in available:
            logger.warning(
                f"⚠️  选定 provider '{self.llm_provider}' 无可用密钥，"
                f"回退到: {available[0]}"
            )
            self.llm_provider = available[0]

    # ── 业务逻辑 ─────────────────────────────────────────────────────────────

    def _build_config(self) -> dict:
        s = self._settings
        cfg = {
            "llm_provider": self.llm_provider,
            "deep_think_llm": self.deep_think_llm,
            "quick_think_llm": self.quick_think_llm,
            "output_language": self.output_language,
            "max_debate_rounds": self.max_debate_rounds,
            "max_risk_discuss_rounds": self._settings.max_risk_discuss_rounds,
            "checkpoint_enabled": self.checkpoint_enabled,
            "data_cache_dir": str(s.cache_dir / "tradingagents"),
            "results_dir": str(s.results_dir),
            "memory_log_path": str(s.memory_log_path),
            "data_vendors": {
                "core_stock_apis": "a_stock",
                "technical_indicators": "a_stock",
                "fundamental_data": "a_stock",
                "news_data": "a_stock",
            },
        }
        if self.backend_url:
            cfg["backend_url"] = self.backend_url
        return cfg

    def _extract_reports(self, state: dict) -> tuple[dict[str, str], dict[str, str]]:
        """
        从 final_state 提取报告。

        Returns
        -------
        (raw_reports, summary_reports)
          raw_reports   — 完整文本（永不截断）
          summary_reports — 每份报告前 500 字符（用于展示和缓存）
        """
        raw: dict[str, str] = {}
        summary: dict[str, str] = {}
        for key in _REPORT_KEYS:
            val = state.get(key)
            if not val:
                continue
            text = val if isinstance(val, str) else json.dumps(val, ensure_ascii=False)
            alias = _REPORT_ALIAS.get(key, key)
            raw[alias] = text
            summary[alias] = text[:SUMMARY_TRUNCATE_LEN]
        return raw, summary

    def _extract_decision(self, final_state: dict) -> tuple[dict, Optional[InvestmentDecision]]:
        """
        从 final_state 提取决策。

        返回：(legacy_dict, investment_decision)
        legacy_dict 保持向后兼容，investment_decision 为结构化对象（可能为 None）。
        """
        decision_text = (
            final_state.get("final_trade_decision", "")
            or final_state.get("trader_investment_plan", "")
            or final_state.get("investment_plan", "")
            or ""
        )
        debate_state = final_state.get("investment_debate_state", {})
        if isinstance(debate_state, dict):
            debate_decision = debate_state.get("final_decision", "")
            if debate_decision:
                decision_text = debate_decision

        # 使用 DecisionAdapter 解析为结构化决策
        adapter = DecisionAdapter()
        inv_decision = adapter.parse(decision_text)

        # 构建 legacy dict（reasoning 保留完整文本，展示时再截断）
        legacy = {
            "signal": inv_decision.signal.value,
            "confidence": inv_decision.confidence,
            "position_size": inv_decision.position_size,
            "reasoning": decision_text,  # 完整保留，不截断
        }
        return legacy, inv_decision

    @smart_retry  # uses self._retry_policy via closure below
    def _analyze_one_retriable(self, ticker: str, date: str) -> StockAnalysisResult:
        """内部方法：带智能重试的 TA 分析（装饰器作用于此处）。"""
        return self._analyze_one_impl(ticker, date)

    def analyze_one(self, ticker: str, date: str) -> StockAnalysisResult:
        """
        TA 单只股票分析入口。

        通过 _analyze_one_retriable 调用，自动处理重试与失败记录。
        """
        ticker = validate_ticker(ticker)
        date = validate_date(date)
        result = StockAnalysisResult(ticker=ticker, date=date)
        t0 = time.time()

        # 缓存检查
        if self._cache:
            _ch = compute_config_hash(self._settings)
            _pv = get_ta_prompt_version(
                self.max_debate_rounds, self._settings.max_risk_discuss_rounds,
                self.output_language, structured_output=True,
            )
            cached = self._cache.get_ta(ticker, date, config_hash=_ch, prompt_ver=_pv)
            if cached:
                logger.debug(f"📦 TA 缓存命中: {ticker}")
                for k, v in cached.items():
                    setattr(result, k, v)
                # 缓存中 investment_decision 是 dict，需还原为对象
                if isinstance(result.investment_decision, dict):
                    result.investment_decision = InvestmentDecision.from_dict(result.investment_decision)
                result.elapsed_sec = 0.0
                return result

        # 使用智能重试执行分析
        try:
            inner_result = self._analyze_one_retriable(ticker, date)
            return inner_result
        except Exception as e:
            # 重试耗尽，记录失败
            result.error = f"{type(e).__name__}: {e}"
            category, desc = classify_error(e)
            store = get_failure_store()
            store.record(ticker, date, "ta", e)
            safe_msg = sanitize_for_log(str(e))
            logger.error(f"❌ {ticker} TA 分析失败 [{category}]: {safe_msg}")
            return result

    def _analyze_one_impl(self, ticker: str, date: str) -> StockAnalysisResult:
        """实际的 TA 分析逻辑（无重试装饰，供重试装饰器调用）。"""
        result = StockAnalysisResult(ticker=ticker, date=date)
        try:
            adapter = self.adapter
            config = adapter.build_config(
                ticker=ticker,
                trade_date=date,
                provider=self.llm_provider,
                model=self.deep_think_llm,
                quick_model=self.quick_think_llm,
                depth=self.max_debate_rounds,
                output_language=self.output_language,
                backend_url=self.backend_url,
                checkpoint=self.checkpoint_enabled,
            )

            logger.info(f"🔍 TA 分析 {ticker} @ {date}")
            analysis_result = adapter.run_analysis(
                ticker, {
                    **config,
                    "extra_kwargs": {
                        "analysts": [
                            "market", "social", "news",
                            "fundamentals", "policy", "hot_money", "lockup",
                        ],
                    },
                }
            )

            if not analysis_result.get("success"):
                raise RuntimeError(
                    analysis_result.get("error", "Analysis failed")
                )

            final_state = analysis_result.get("final_state", {})
            legacy, inv_decision = self._extract_decision(final_state)

            result.signal = legacy["signal"]
            result.confidence = legacy["confidence"]
            result.position_size = legacy["position_size"]
            result.reasoning = legacy["reasoning"]  # 完整 reasoning
            raw_reports, summary_reports = self._extract_reports(final_state)
            result.reports = summary_reports      # 展示用：500字摘要
            result.reports_raw = raw_reports      # 存储用：完整报告
            result.risk_assessment = final_state.get(
                "risk_debate_state", {}
            )
            if isinstance(result.risk_assessment, (dict, list)):
                result.risk_assessment = json.dumps(
                    result.risk_assessment, ensure_ascii=False
                )

            result.decision_raw = legacy
            result.investment_decision = inv_decision

            logger.info(
                f"✅ {ticker}: signal={result.signal} "
                f"({time.time()-t0:.0f}s)"
            )

            # 写缓存
            if self._cache:
                _ch = compute_config_hash(self._settings)
                _pv = get_ta_prompt_version(
                    self.max_debate_rounds, self._settings.max_risk_discuss_rounds, self.output_language
                )
                self._cache.set_ta(ticker, date, result.to_dict(), config_hash=_ch, prompt_ver=_pv)

        except TradeKronoError as e:
            # 已知业务错误：记录完整信息
            result.error = f"{type(e).__name__}: {e}"
            logger.error(f"❌ {ticker} TA 分析失败: {e}")
        except Exception as e:
            # 未预料错误：脱敏记录
            result.error = f"{type(e).__name__}: {e}"
            safe_msg = sanitize_for_log(str(e))
            logger.error(f"❌ {ticker} TA 分析失败: {safe_msg}")

        finally:
            result.elapsed_sec = round(time.time() - t0, 2)

        return result

    def analyze_batch(
        self,
        tickers: list[str],
        date: str,
        progress_cb: Optional[Callable[[int, int, StockAnalysisResult], None]] = None,
    ) -> list[StockAnalysisResult]:
        date = validate_date(date)
        tickers = [validate_ticker(t) for t in tickers]
        total = len(tickers)
        results: list[StockAnalysisResult] = []

        logger.info(f"🚀 TA 批量分析启动: {total} 只, date={date}")
        for idx, tk in enumerate(tickers, 1):
            res = self.analyze_one(tk, date)
            results.append(res)
            if progress_cb:
                try:
                    progress_cb(idx, total, res)
                except Exception:
                    pass
        success = sum(1 for r in results if r.error is None)
        logger.info(f"📊 TA 批量分析完成: 成功 {success}/{total}")
        return results

    def save_results(self, results: list[StockAnalysisResult], path: str) -> str:
        data = [r.to_dict() for r in results]
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        logger.info(f"💾 TA 结果已保存: {path}")
        return path

    def save_raw_reports(
        self, results: list[StockAnalysisResult], date: str,
        results_dir: Optional[Path] = None,
    ) -> dict[str, str]:
        """
        将每只股票的完整原始报告写入磁盘。

        路径格式：{results_dir}/raw/{date}/{ticker}.json

        每个文件包含：
          - reports_raw: 完整 TA 各维度报告（市场/情绪/新闻/基本面/政策等）
          - decision_text: 完整决策文本（含 Debate 过程）
          - risk_assessment: 完整风险评估
          - investment_decision: 结构化决策（Signal + Confidence + Thesis + Risks）
          - metadata: 分析时间戳、耗时等

        用途：AI 复盘、策略回测、历史研究、RAG、Agent memory
        """
        base_dir = results_dir or self._settings.results_dir
        raw_dir = base_dir / "raw" / date
        raw_dir.mkdir(parents=True, exist_ok=True)

        written: dict[str, str] = {}  # ticker → file_path
        for r in results:
            if r.error:
                continue
            file_data: dict = {
                "ticker": r.ticker,
                "date": r.date,
                "analyzed_at": datetime.now().isoformat(),
                "elapsed_sec": r.elapsed_sec,
                "reports_raw": r.reports_raw,
                "decision_text": r.reasoning or "",
                "risk_assessment": r.risk_assessment or "",
                "investment_decision": (
                    r.investment_decision.to_dict()
                    if r.investment_decision else None
                ),
            }
            path = raw_dir / f"{r.ticker}.json"
            with open(path, "w", encoding="utf-8") as f:
                json.dump(file_data, f, ensure_ascii=False, indent=2)
            written[r.ticker] = str(path)

        logger.info(
            f"💾 原始报告已写入: {raw_dir} ({len(written)} 只)"
        )
        return written

    @staticmethod
    def load_raw_report(ticker: str, date: str, results_dir: Optional[Path] = None, settings: Optional[Settings] = None) -> Optional[dict]:
        """从磁盘加载某只股票的原始报告。"""
        rd = results_dir or (settings or get_settings()).results_dir
        path = rd / "raw" / date / f"{ticker}.json"
        if not path.exists():
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
