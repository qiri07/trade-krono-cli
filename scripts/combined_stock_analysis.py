#!/usr/bin/env python3
"""综合选股汇总结算：交集股票分析 + AI 核实 + 飞书推送（汇总 + 逐只）。

对当日筛选流程中「多条选股线共有的股票」做交集汇总：
  ① 白名单/巴菲特（合并为单一来源）  ② 三线共振(MACD)  ③ 多头排列  ④ 双均线金叉

命中 ≥2 条选股线的股票即进入交集候选池，对每只候选：
  - 标注命中的选股线来源
  - 做基础技术分析（MA 排列 / 趋势 / 量能 / 综合分）
  - 做基本面评估（PE/PB/估值分/估值结论）
  - 按命中条数加权排序（共振越多越靠前）

可选增强（推送前填充，写入每只卡片）：
  - --kronos 1：批量跑 Kronos K 线预测（首次加载模型约 1-3 分钟）
  - --backtest 1：逐只读取 research DB 历史信号做单股信号回测

随后 AI 核实 + 飞书推送（汇总卡片 + 逐只卡片）。

用法：
  uv run python scripts/combined_stock_analysis.py
  uv run python scripts/combined_stock_analysis.py --date 2026-10-15 --min-sources 2
  uv run python scripts/combined_stock_analysis.py --push 0   # 跳过飞书推送
  uv run python scripts/combined_stock_analysis.py --per-stock-push 0  # 跳过逐只推送
  uv run python scripts/combined_stock_analysis.py --kronos 1 --backtest 1  # 卡片含预测+回测
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger

from scripts._utils import normalize_ticker_key
from scripts.daily_analysis import ai_verify, analyze_stock
from scripts.feishu_utils import push_feishu
from trade_krono_cli.cli_commands._core_helpers import _load_env
from trade_krono_cli.domain.kronos_result import KronosForecastResult
from trade_krono_cli.eval_data import EvalRecord
from trade_krono_cli.research_db import ResearchDatabase

# ── 结果文件路径（按日期）────────────────────────────────────────────────
_RESULTS = Path("outputs/results")

# 各选股线结果文件名模板（date_compact 无连字符，date_dash 有连字符）
_TEMPLATES: dict[str, str] = {
    "buffett": "buffett_screen_{dc}.txt",
    "buffett_wl": "buffett_dynamic_whitelist.txt",
    "resonance": "macd_resonance_{dd}.json",
    "multi_head": "multi_head_{dc}.txt",
    "double_ma": "double_ma_golden_cross_{dd}.json",
}

# 选股线中文名（用于展示）
_SOURCE_LABELS: dict[str, str] = {
    "whitelist": "白名单/巴菲特",
    "resonance": "三线共振",
    "multi_head": "多头排列",
    "double_ma": "双均线金叉",
}


@dataclass
class TechView:
    """analyze_stock() 返回结果的技术分析边界视图。

    将松散的 dict[str, object] 收敛为带类型字段的 dataclass，
    供卡片构建 / LLM 核实 / 结果落盘统一消费，避免反复 an.get(...)。
    """

    name: str = ""
    trend: str = ""
    ma_signal: str = ""
    vol_signal: str = ""
    score: int | float = 0

    @classmethod
    def from_analysis(cls, an: dict[str, object] | None) -> TechView:
        """从 analyze_stock() 的 dict 结果构建 TechView，缺失字段取默认值。"""
        if an is None:
            return cls()
        raw = an.get("score", 0)
        if isinstance(raw, (int, float)):
            score: int | float = raw
        elif isinstance(raw, str):
            try:
                score = float(raw)
            except ValueError:
                score = 0
        else:
            score = 0
        return cls(
            name=str(an.get("name", "")),
            trend=str(an.get("trend", "")),
            ma_signal=str(an.get("ma_signal", "")),
            vol_signal=str(an.get("vol_signal", "")),
            score=score,
        )

    def to_payload(self) -> dict[str, object]:
        """导出为落盘 / LLM 视图用的 dict。"""
        return {
            "name": self.name,
            "trend": self.trend,
            "ma_signal": self.ma_signal,
            "vol_signal": self.vol_signal,
            "score": self.score,
        }


@dataclass
class FundView:
    """基本面数据边界视图（来自 analyze_stock 的估值字段）。"""

    pe_ttm: float | None = None
    pb: float | None = None
    valuation_score: float | None = None
    valuation_conclusion: str = ""

    @classmethod
    def from_analysis(cls, an: dict[str, object] | None) -> FundView:
        """从 analyze_stock() 结果构建 FundView，缺失字段取默认值。

        信任调用方类型契约（analyze_stock 返回的 dict schema 固定），
        用 _coerce_float 收敛 int/float 到 float | None。
        """
        if an is None:
            return cls()

        def _coerce(key: str) -> float | None:
            """将 dict 值收敛为 float | None（int 提升为 float，bool/非数字视为 None）。"""
            v = an.get(key)
            if isinstance(v, bool):
                return None
            if isinstance(v, (int, float)):
                return float(v)
            return None

        return cls(
            pe_ttm=_coerce("pe_ttm"),
            pb=_coerce("pb"),
            valuation_score=_coerce("valuation_score"),
            valuation_conclusion=str(an.get("valuation_conclusion") or ""),
        )

    def to_payload(self) -> dict[str, object]:
        """导出为落盘 / LLM 视图用的 dict。"""
        return {
            "pe_ttm": self.pe_ttm,
            "pb": self.pb,
            "valuation_score": self.valuation_score,
            "valuation_conclusion": self.valuation_conclusion,
        }

    def summary_line(self) -> str:
        """单行基本面摘要（用于飞书卡片）。"""
        parts: list[str] = []
        if self.pe_ttm is not None:
            parts.append(f"PE={self.pe_ttm:.1f}")
        if self.pb is not None:
            parts.append(f"PB={self.pb:.1f}")
        if self.valuation_score is not None:
            parts.append(f"估值分={self.valuation_score:.0f}")
        if self.valuation_conclusion:
            parts.append(self.valuation_conclusion)
        return "  ".join(parts) if parts else "无基本面数据"


@dataclass
class KronosView:
    """Kronos 预测边界视图（来自 KronosForecastResult）。

    将 Kronos 预测结果收敛为带类型字段的 dataclass，
    供卡片构建 / LLM 核实 / 结果落盘统一消费。
    """

    direction: str = ""  # UP / DOWN / FLAT
    expected_change_pct: float | None = None
    last_close: float | None = None
    predicted_close: float | None = None
    confidence_low: float | None = None
    confidence_high: float | None = None
    volatility_proxy: float | None = None
    horizon: int = 0

    @classmethod
    def from_result(cls, res: KronosForecastResult | None) -> KronosView:
        """从 KronosForecastResult 构建 KronosView，缺失字段取默认值。"""
        if res is None:
            return cls()
        band = res.confidence_band or {}
        low = band.get("low")
        high = band.get("high")
        return cls(
            direction=res.direction or "",
            expected_change_pct=(
                float(res.expected_change_pct)
                if isinstance(res.expected_change_pct, (int, float))
                else None
            ),
            last_close=(
                float(res.last_close) if isinstance(res.last_close, (int, float)) else None
            ),
            predicted_close=(
                float(res.predicted_close_final)
                if isinstance(res.predicted_close_final, (int, float))
                else None
            ),
            confidence_low=(
                float(low) if isinstance(low, (int, float)) and not isinstance(low, bool) else None
            ),
            confidence_high=(
                float(high)
                if isinstance(high, (int, float)) and not isinstance(high, bool)
                else None
            ),
            volatility_proxy=(
                float(res.volatility_proxy)
                if isinstance(res.volatility_proxy, (int, float))
                else None
            ),
            horizon=int(res.horizon) if isinstance(res.horizon, int) else 0,
        )

    def to_payload(self) -> dict[str, object]:
        """导出为落盘 / LLM 视图用的 dict。"""
        return {
            "direction": self.direction,
            "expected_change_pct": self.expected_change_pct,
            "last_close": self.last_close,
            "predicted_close": self.predicted_close,
            "confidence_low": self.confidence_low,
            "confidence_high": self.confidence_high,
            "volatility_proxy": self.volatility_proxy,
            "horizon": self.horizon,
        }

    @property
    def available(self) -> bool:
        """是否有有效预测数据。"""
        return bool(self.direction) or self.expected_change_pct is not None

    def summary_line(self) -> str:
        """单行 Kronos 预测摘要（用于飞书卡片）。"""
        if not self.available:
            return "无 Kronos 预测数据"
        parts: list[str] = []
        if self.direction:
            parts.append(f"方向={self.direction}")
        if self.expected_change_pct is not None:
            seg = f"预期 {self.expected_change_pct:+.1f}%"
            if self.last_close is not None and self.predicted_close is not None:
                seg += f" ({self.last_close:.2f}→{self.predicted_close:.2f})"
            parts.append(seg)
        if self.confidence_low is not None and self.confidence_high is not None:
            parts.append(f"置信[{self.confidence_low:+.1f}%,{self.confidence_high:+.1f}%]")
        if self.horizon:
            parts.append(f"预测{self.horizon}日")
        return "  ".join(parts)


@dataclass
class BacktestView:
    """该股信号的历史回测边界视图。

    由 attach_backtest 从 research DB 的信号 + 实际价格构建，
    展示该股历史 TA/Kronos 信号的累计回测表现。
    """

    total_return_pct: float | None = None
    sharpe_ratio: float | None = None
    max_drawdown_pct: float | None = None
    win_rate_pct: float | None = None
    n_trades: int = 0
    n_signals: int = 0
    horizon_days: int = 0
    window_days: int = 0
    available: bool = False

    def to_payload(self) -> dict[str, object]:
        """导出为落盘 / LLM 视图用的 dict。"""
        return {
            "total_return_pct": self.total_return_pct,
            "sharpe_ratio": self.sharpe_ratio,
            "max_drawdown_pct": self.max_drawdown_pct,
            "win_rate_pct": self.win_rate_pct,
            "n_trades": self.n_trades,
            "n_signals": self.n_signals,
            "horizon_days": self.horizon_days,
            "window_days": self.window_days,
            "available": self.available,
        }

    def summary_line(self) -> str:
        """单行回测摘要（用于飞书卡片）。"""
        if not self.available:
            return "无历史回测数据"
        m = {
            "sharpe_ratio": self.sharpe_ratio,
            "max_drawdown_pct": self.max_drawdown_pct,
            "win_rate_pct": self.win_rate_pct,
        }
        parts: list[str] = [
            f"近{self.window_days}天 {self.n_signals} 笔信号 "
            f"(horizon {self.horizon_days}日, {self.n_trades} 笔成交)"
        ]
        if self.total_return_pct is not None:
            parts.append(f"组合总收益 {self.total_return_pct:+.1f}%")
        if m["sharpe_ratio"] is not None:
            parts.append(f"夏普 {m['sharpe_ratio']:.2f}")
        if m["max_drawdown_pct"] is not None:
            parts.append(f"最大回撤 {m['max_drawdown_pct']:.1f}%")
        if m["win_rate_pct"] is not None:
            parts.append(f"胜率 {m['win_rate_pct']:.0f}%")
        return "  ".join(parts)


@dataclass
class StockCandidate:
    """交集候选股票，命中多条选股线后做技术分析。"""

    code: str
    sources: list[str] = field(default_factory=list)
    hit_count: int = 0
    analysis: dict[str, object] | None = None
    kronos: KronosForecastResult | None = None
    backtest: BacktestView | None = None

    @property
    def tech(self) -> TechView:
        """分析结果的技术边界视图（无数据时为默认 TechView）。"""
        return TechView.from_analysis(self.analysis)

    @property
    def fund(self) -> FundView:
        """分析结果的基本面边界视图（无数据时为默认 FundView）。"""
        return FundView.from_analysis(self.analysis)

    @property
    def kronos_view(self) -> KronosView:
        """Kronos 预测边界视图（未开启时为默认 KronosView）。"""
        return KronosView.from_result(self.kronos)

    @property
    def rank_score(self) -> int:
        """共振加权排序分：命中条数 * 10 + 技术分。"""
        return self.hit_count * 10 + int(self.tech.score)


def _date_tags(date_str: str) -> tuple[str, str]:
    """返回 (无连字符, 有连字符) 两种日期格式。"""
    return date_str.replace("-", ""), date_str


def _codes_from_buffett_screen(path: Path) -> set[str]:
    """从巴菲特筛选 txt 提取通过股票的 6 位代码集合（排除北交所）。"""
    if not path.exists():
        return set()
    text = path.read_text(encoding="utf-8")
    codes: set[str] = set()
    in_table = False
    for line in text.splitlines():
        if "代码" in line and "名称" in line:
            in_table = True
            continue
        if not in_table:
            continue
        stripped = line.strip()
        if not stripped or "--------" in stripped:
            continue
        if "失败分布" in stripped:
            break
        m = re.match(r"^\s*(\d{6}(?:\.\w+)?)\s+(\S+)", stripped)
        if not m:
            continue
        raw = m.group(1)
        code6 = raw.split(".")[0]
        # 排除北交所（920 开头或 .BJ 后缀）
        if code6.startswith("920") or raw.upper().endswith(".BJ"):
            continue
        codes.add(code6)
    return codes


def _codes_from_buffett_whitelist(path: Path) -> set[str]:
    """从动态白名单文件读取 6 位代码集合（排除北交所）。"""
    if not path.exists():
        return set()
    text = path.read_text(encoding="utf-8").strip()
    codes: set[str] = set()
    for c in text.split(","):
        c = c.strip()
        if not c:
            continue
        code6 = c.split(".")[0]
        if code6.startswith("920") or c.upper().endswith(".BJ"):
            continue
        codes.add(code6)
    return codes


def _codes_from_resonance(path: Path) -> set[str]:
    """从三线共振 JSON 提取 ticker 集合。"""
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return set()
    codes: set[str] = set()
    for item in data if isinstance(data, list) else []:
        ticker = item.get("ticker", "") if isinstance(item, dict) else ""
        if ticker:
            codes.add(normalize_ticker_key(ticker).split(".")[1])
    return codes


def _codes_from_multi_head(path: Path) -> set[str]:
    """从多头排列 txt 提取全部带前缀 ticker 的 6 位代码。"""
    if not path.exists():
        return set()
    text = path.read_text(encoding="utf-8")
    found = set(re.findall(r"\b(?:sh|sz|bj|hk)\.(\d{6})", text))
    return found


def _codes_from_double_ma(path: Path) -> set[str]:
    """从双均线金叉 JSON 提取 ticker 集合。"""
    if not path.exists():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return set()
    codes: set[str] = set()
    for item in data if isinstance(data, list) else []:
        ticker = item.get("ticker", "") if isinstance(item, dict) else ""
        if ticker:
            codes.add(normalize_ticker_key(ticker).split(".")[1])
    return codes


def collect_source_sets(date_str: str) -> dict[str, set[str]]:
    """汇总各选股线的 6 位代码集合。

    返回 {来源键: 6 位代码集合}。

    来源键说明：
      - whitelist: 白名单/巴菲特（三路并集后仅计 1 个来源，避免交集计数虚高）
        · 巴菲特动态白名单 buffett_dynamic_whitelist.txt
        · 当日巴菲特筛选结果 buffett_screen_{dc}.txt
        · 当日 daily_analysis 分析目录 outputs/results/<code>/
      - resonance: 三线共振（MACD DIF）macd_resonance_{dd}.json
      - multi_head: 多头排列 multi_head_{dc}.txt
      - double_ma: 双均线金叉 double_ma_golden_cross_{dd}.json

    注意：白名单/巴菲特被合并为单一 "whitelist" 来源参与命中计数；
    其余三条选股线独立计数。
    """
    dc, dd = _date_tags(date_str)
    fmaps = {k: t.format(dc=dc, dd=dd) for k, t in _TEMPLATES.items()}

    result: dict[str, set[str]] = {}

    # 白名单三路并集：动态白名单 + 当日筛选 + 当日分析目录
    whitelist_codes = _codes_from_buffett_whitelist(_RESULTS / fmaps["buffett_wl"])
    whitelist_codes |= _codes_from_buffett_screen(_RESULTS / fmaps["buffett"])
    for d in _RESULTS.iterdir() if _RESULTS.exists() else []:
        if d.is_dir() and re.fullmatch(r"\d{6}|hk\.\d{5}", d.name):
            whitelist_codes.add(normalize_ticker_key(d.name).split(".")[1])
    result["whitelist"] = whitelist_codes

    result["resonance"] = _codes_from_resonance(_RESULTS / fmaps["resonance"])
    result["multi_head"] = _codes_from_multi_head(_RESULTS / fmaps["multi_head"])
    result["double_ma"] = _codes_from_double_ma(_RESULTS / fmaps["double_ma"])

    return result


def build_intersection(
    source_sets: dict[str, set[str]], min_sources: int
) -> list[StockCandidate]:
    """按命中条数过滤并排序，返回交集候选池。

    排序规则：命中条数降序。
    """
    counter: Counter[str] = Counter()
    for codes in source_sets.values():
        for c in codes:
            counter[c] += 1

    cands = [
        StockCandidate(code=c, hit_count=n)
        for c, n in counter.items()
        if n >= min_sources
    ]

    # 标注每条命中来源（保留展示顺序）
    src_by_code: dict[str, list[str]] = {}
    for src, codes in source_sets.items():
        for c in codes:
            src_by_code.setdefault(c, []).append(src)
    for cand in cands:
        cand.sources = src_by_code.get(cand.code, [])

    cands.sort(key=lambda x: -x.hit_count)
    return cands


def analyze_candidates(
    cands: list[StockCandidate], date_str: str, max_analyze: int
) -> list[StockCandidate]:
    """对候选做技术分析，仅保留有数据且评分较高的，按命中数+分数排序。"""
    analyzed: list[StockCandidate] = []
    for cand in cands[:max_analyze]:
        code = cand.code
        ticker = normalize_ticker_key(code)
        res = analyze_stock(ticker, end_date=date_str)
        if res.get("status") != "ok":
            logger.debug(f"  {code} 无数据，跳过")
            continue
        cand.analysis = res
        analyzed.append(cand)

    analyzed.sort(key=lambda x: -x.rank_score)
    return analyzed


def attach_kronos(cands: list[StockCandidate], date_str: str) -> int:
    """对候选批量跑 Kronos 预测，写回 cand.kronos，返回成功填充数量。

    懒加载 KronosSession（此时才导入/加载模型），批量预测走缓存+分批+降级；
    任何异常均不阻断主流程（log warning 后返回已填充数量）。
    """
    if not cands:
        return 0
    try:
        from trade_krono_cli.models.kronos_session import KronosSession  # noqa: PLC0415

        tickers = [normalize_ticker_key(c.code) for c in cands]
        session = KronosSession()
        results = session.runner.predict_batch(tickers, date_str)
        by_ticker = {r.ticker: r for r in results}
        n_ok = 0
        for cand in cands:
            res = by_ticker.get(normalize_ticker_key(cand.code))
            if res is not None and not res.error:
                cand.kronos = res
                n_ok += 1
        logger.info(f"🧠 Kronos 预测完成：{n_ok}/{len(cands)} 只成功")
        return n_ok
    except Exception as e:
        logger.warning(f"Kronos 预测整体失败（不影响推送）: {type(e).__name__}: {e}")
        return 0


def _build_eval_records(
    ticker: str, date_str: str, days: int, horizon: int
) -> list[EvalRecord]:
    """从 research DB 读取该 ticker 近 days 天的历史信号，构造 EvalRecord 列表。

    优先读 `ta_analysis` 表（独立分析脚本实际写入的表），`signals` 表（pipeline
    post_processing 写入）作为 fallback —— 二者取到即视为该日有信号。复用
    eval_data 的价格获取（±5 日回退）与成本扣减；无信号 / 无价格时返回空列表。
    按 (job, ticker) 粒度取每个 job 的信号，同一天多 job 仅取首个。
    """
    from trade_krono_cli.eval_data import (
        apply_roundtrip_cost,
        calc_return,
        get_close_price,
    )
    from trade_krono_cli.research_db import get_research

    research = get_research()
    jobs = research.list_jobs(limit=2000)
    window_start = (
        datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=days)
    ).strftime("%Y-%m-%d")
    norm_ticker = normalize_ticker_key(ticker)

    records: list[EvalRecord] = []
    seen_dates: set[str] = set()
    for job in jobs:
        job_date = str(job.get("date") or "")
        if not job_date or job_date < window_start or job_date > date_str:
            continue
        if job_date in seen_dates:
            continue
        job_id = str(job.get("job_id") or "")
        if not job_id:
            continue

        sig = _lookup_signal(research, job_id, norm_ticker)
        if sig is None:
            continue
        if sig["has_error"]:
            continue
        seen_dates.add(job_date)

        entry_price = get_close_price(norm_ticker, job_date)
        if entry_price is None or entry_price <= 0:
            continue
        exit_date = (
            datetime.strptime(job_date, "%Y-%m-%d") + timedelta(days=horizon)
        ).strftime("%Y-%m-%d")
        exit_price = get_close_price(norm_ticker, exit_date)
        if exit_price is None or exit_price <= 0:
            continue

        gross = calc_return(entry_price, exit_price)
        net = apply_roundtrip_cost(gross, 17.0)
        pred_dir = sig["kronos_direction"]
        pred_ret = sig["kronos_change"]
        actual_dir = "UP" if net > 1.0 else ("DOWN" if net < -1.0 else "FLAT")
        is_correct = bool(pred_dir) and pred_dir != "FLAT" and pred_dir == actual_dir
        error = (float(pred_ret) - gross) if isinstance(pred_ret, (int, float)) else 0.0

        records.append(
            EvalRecord(
                ticker=norm_ticker,
                eval_date=job_date,
                horizon_days=horizon,
                pred_direction=pred_dir,
                pred_return_pct=(
                    float(pred_ret) if isinstance(pred_ret, (int, float)) else None
                ),
                actual_return_pct=round(net, 4),
                actual_direction=actual_dir,
                is_direction_correct=is_correct,
                error_pct=round(error, 4),
                ta_signal=sig["ta_signal"],
                composite_score=(
                    float(sig["composite_score"])
                    if isinstance(sig["composite_score"], (int, float))
                    else None
                ),
            ),
        )
    return records


def _lookup_signal(
    research: ResearchDatabase, job_id: str, norm_ticker: str
) -> dict | None:
    """取某 job 中某 ticker 的历史信号，统一为内部字段。

    优先 `ta_analysis`（独立分析脚本写入），fallback `signals`（pipeline 写入）。
    统一字段：ta_signal / kronos_direction / kronos_change / composite_score / has_error。
    """
    norm_sig = next(
        (
            s
            for s in research.get_ta_by_job(job_id)
            if normalize_ticker_key(str(s.get("ticker") or "")) == norm_ticker
        ),
        None,
    )
    if norm_sig is not None:
        has_error = bool(norm_sig.get("error"))
        return {
            "ta_signal": norm_sig.get("signal"),
            "kronos_direction": None,
            "kronos_change": None,
            "composite_score": None,
            "has_error": has_error,
        }

    sig = next(
        (
            s
            for s in research.get_signals_by_job(job_id)
            if normalize_ticker_key(str(s.get("ticker") or "")) == norm_ticker
        ),
        None,
    )
    if sig is not None:
        both_errors = bool(sig.get("ta_error")) and bool(sig.get("kronos_error"))
        return {
            "ta_signal": sig.get("ta_signal"),
            "kronos_direction": sig.get("kronos_direction") or None,
            "kronos_change": sig.get("kronos_change"),
            "composite_score": sig.get("composite_score"),
            "has_error": both_errors,
        }
    return None


def attach_backtest(
    cands: list[StockCandidate], date_str: str, days: int = 90, horizon: int = 5
) -> int:
    """逐只读取该 ticker 的历史信号并跑单股回测，写回 cand.backtest。

    返回成功填充数量（含「无信号」降级为 available=False 的也计入尝试，
    实际填充 available=True 的数量）。任何异常不阻断主流程。
    """
    if not cands:
        return 0
    n_ok = 0
    for cand in cands:
        try:
            from trade_krono_cli.prediction_eval_backtest import run_backtest  # noqa: PLC0415

            eval_records = _build_eval_records(cand.code, date_str, days, horizon)
            if not eval_records:
                cand.backtest = BacktestView(
                    horizon_days=horizon, window_days=days, available=False
                )
                continue
            bt = run_backtest(eval_records, rebal_mode="fixed_horizon", fixed_horizon=horizon)
            metrics = bt.metrics or {}
            has_signal_history = bool(eval_records)
            cand.backtest = BacktestView(
                total_return_pct=bt.total_return_pct if has_signal_history else None,
                sharpe_ratio=metrics.get("sharpe_ratio") or None,
                max_drawdown_pct=metrics.get("max_drawdown_pct") or None,
                win_rate_pct=metrics.get("win_rate_pct") or None,
                n_trades=bt.n_trades,
                n_signals=len(eval_records),
                horizon_days=horizon,
                window_days=days,
                available=has_signal_history,
            )
            if has_signal_history:
                n_ok += 1
        except Exception as e:
            logger.warning(f"  {cand.code} 历史回测失败: {type(e).__name__}: {e}")
            cand.backtest = BacktestView(
                horizon_days=horizon, window_days=days, available=False
            )
    logger.info(f"📈 历史回测完成：{n_ok}/{len(cands)} 只成功")
    return n_ok


def _build_stock_card(
    cand: StockCandidate, date_str: str, ai_note: str = ""
) -> str:
    """构建单只股票的飞书卡片文本（技术面 + 基本面 + Kronos 预测 + 历史回测 + AI 核实）。"""
    tv = cand.tech
    fv = cand.fund
    kv = cand.kronos_view
    bv = cand.backtest
    code = cand.code
    name = tv.name or code
    label = "+".join(_SOURCE_LABELS.get(s, s) for s in cand.sources)

    lines: list[str] = []
    lines.append(f"🔗 **{date_str} 综合选股 · {code} {name}**")
    lines.append("")
    lines.append(f"命中来源：{label}（{cand.hit_count} 条线共振）")
    lines.append("")
    lines.append("── 技术面 ──")
    lines.append(f"  技术分: {tv.score}  趋势: {tv.trend}")
    lines.append(f"  均线信号: {tv.ma_signal}  量能: {tv.vol_signal}")
    lines.append("")
    lines.append("── 基本面 ──")
    lines.append(f"  {fv.summary_line()}")
    if kv.available:
        lines.append("")
        lines.append("── Kronos 预测 ──")
        lines.append(f"  {kv.summary_line()}")
    if bv is not None:
        lines.append("")
        lines.append("── 历史回测 ──")
        lines.append(f"  {bv.summary_line()}")
    if ai_note:
        lines.append("")
        lines.append("── AI 核实 ──")
        lines.append(f"  {ai_note}")
    return "\n".join(lines)


def _push_per_stock(cands: list[StockCandidate], date_str: str) -> None:
    """逐只推送飞书卡片（每只含技术面 + 基本面），间隔 1s 防 webhook 限流。"""
    logger.info(f"📤 开始逐只推送 {len(cands)} 只股票...")
    for i, cand in enumerate(cands, 1):
        name = cand.tech.name or cand.code
        stock_card = _build_stock_card(cand, date_str)
        title = f"🔗 {date_str} 综合选股 {i}/{len(cands)} {cand.code} {name}"
        ok = push_feishu("text", content=stock_card, title=title)
        logger.info(f"  {'✅' if ok else '❌'} [{i}/{len(cands)}] {cand.code} {name}")
        if i < len(cands):
            time.sleep(1)


def _build_combined_card(
    cands: list[StockCandidate], date_str: str, source_sets: dict[str, set[str]]
) -> str:
    """构建飞书汇总卡片文本（含技术面 + 基本面 + AI 核实）。"""
    lines: list[str] = []
    lines.append(f"🔗 **{date_str} 综合选股 · 多线共振汇总**")
    lines.append("")
    lines.append("各选股线命中数量：")
    for key, label in _SOURCE_LABELS.items():
        n = len(source_sets.get(key, set()))
        lines.append(f"  · {label}：{n} 只")
    lines.append("")

    if not cands:
        lines.append("⚠️ 无命中 ≥2 条选股线的股票。")
        return "\n".join(lines)

    lines.append(f"📊 命中 ≥2 条选股线的股票（{len(cands)} 只，按共振度排序）：")
    lines.append("")
    for cand in cands:
        code = cand.code
        tv = cand.tech
        fv = cand.fund
        name = tv.name
        label = "+".join(_SOURCE_LABELS.get(s, s) for s in cand.sources)
        if cand.analysis:
            lines.append(f"  🔗 **{code} {name}**（{label}）")
            lines.append(
                f"     技术分={tv.score}  趋势={tv.trend}  "
                f"均线={tv.ma_signal}  量能={tv.vol_signal}"
            )
            lines.append(f"     基本面: {fv.summary_line()}")
            kv = cand.kronos_view
            if kv.available:
                lines.append(f"     预测: {kv.summary_line()}")
            bv = cand.backtest
            if bv is not None:
                lines.append(f"     回测: {bv.summary_line()}")
        else:
            lines.append(f"  🔗 **{code} {name}**（{label}，无技术数据）")
    return "\n".join(lines)


def _llm_view(c: StockCandidate) -> dict[str, object]:
    """将候选转为 LLM 核实输入（ticker + 技术 + 基本面 + Kronos 预测指标）。"""
    view = c.tech.to_payload()
    fund = c.fund.to_payload()
    payload: dict[str, object] = {"ticker": normalize_ticker_key(c.code), **view, **fund}
    kv = c.kronos_view
    if kv.available:
        payload["kronos"] = kv.to_payload()
    return payload


def main() -> None:
    """主流程：汇总交集 → 技术分析 → AI 核实 → 飞书推送。"""
    parser = argparse.ArgumentParser(description="综合选股交集分析 + 飞书推送")
    parser.add_argument(
        "--date",
        default=datetime.now().strftime("%Y-%m-%d"),
        help="分析日期 YYYY-MM-DD（默认今日）",
    )
    parser.add_argument(
        "--min-sources",
        type=int,
        default=2,
        help="最少命中几条选股线才进入交集池（默认 2）",
    )
    parser.add_argument(
        "--max-analyze",
        type=int,
        default=60,
        help="最多对前 N 只做技术分析（默认 60）",
    )
    parser.add_argument(
        "--push",
        type=int,
        default=1,
        help="是否飞书推送（1=是，0=否，默认 1）",
    )
    parser.add_argument(
        "--per-stock-push",
        type=int,
        default=1,
        help="是否逐只推送飞书（1=是，0=否，默认 1；需 --push=1 同时开启）",
    )
    parser.add_argument(
        "--kronos",
        type=int,
        default=0,
        help="逐只推送前对候选跑 Kronos 预测（1=是，0=否，默认 0；首次加载模型约 1-3 分钟）",
    )
    parser.add_argument(
        "--backtest",
        type=int,
        default=0,
        help="逐只推送前对候选做该股历史信号回测（1=是，0=否，默认 0）",
    )
    parser.add_argument(
        "--backtest-days",
        type=int,
        default=90,
        help="回测信号回溯窗口（天，默认 90）",
    )
    parser.add_argument(
        "--backtest-horizon",
        type=int,
        default=5,
        help="回测持仓周期（交易日，默认 5）",
    )
    args = parser.parse_args()

    _load_env()

    logger.info("=" * 60)
    logger.info(
        f"🔗 综合选股交集分析启动  日期={args.date}  "
        f"min_sources={args.min_sources}"
    )
    logger.info("=" * 60)

    # ── Step 1: 汇总各选股线代码集合 ─────────────────────────────
    source_sets = collect_source_sets(args.date)
    logger.info(
        "各选股线命中数量："
        + " ".join(f"{k}={len(v)}" for k, v in source_sets.items())
    )

    # ── Step 2: 交集候选池（≥N 条线）────────────────────────────
    cands = build_intersection(source_sets, args.min_sources)
    logger.info(
        f"命中 ≥{args.min_sources} 条选股线的股票：{len(cands)} 只"
        f"（按共振度排序，前 {min(len(cands), 20)} 只如下）"
    )
    for c in cands[:20]:
        logger.info(
            f"  🔗 {c.code}  命中 {c.hit_count} 条: "
            f"{'+'.join(c.sources)}"
        )

    # ── Step 3: 对候选做技术分析 ────────────────────────────────
    logger.info(f"\n📈 对前 {args.max_analyze} 只候选做技术分析...")
    analyzed = analyze_candidates(cands, args.date, args.max_analyze)
    logger.info(f"  有效技术分析结果：{len(analyzed)} 只")

    # ── Step 4: 汇总文件路径（在 Step 5.6 回测 attach 之后写出，确保 kronos/backtest 完整）──
    dc = args.date.replace("-", "")
    out_file = _RESULTS / f"combined_intersection_{dc}.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)

    def _write_intersection_json() -> None:
        """构造并写出交集汇总 JSON（kronos / backtest 字段在此时已填充）。"""
        out_payload = {
            "date": args.date,
            "min_sources": args.min_sources,
            "source_counts": {k: len(v) for k, v in source_sets.items()},
            "intersection_total": len(cands),
            "analyzed": [
                {
                    "code": c.code,
                    "hit_count": c.hit_count,
                    "sources": c.sources,
                    **c.tech.to_payload(),
                    **c.fund.to_payload(),
                    "kronos": (
                        c.kronos_view.to_payload() if c.kronos is not None else None
                    ),
                    "backtest": (
                        c.backtest.to_payload() if c.backtest is not None else None
                    ),
                }
                for c in analyzed
            ],
        }
        out_file.write_text(
            json.dumps(out_payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info(f"💾 结果已保存: {out_file}")

    # ── Step 5: AI 核实（聚合交集 + 多线共振视角）────────────────
    logger.info("🤖 启动 AI 核实...")
    ai_summary = ""
    try:
        ai_results_for_llm = [_llm_view(c) for c in analyzed]
        ai_result = ai_verify(ai_results_for_llm, args.date)
        ai_summary = (
            f"分析摘要：{ai_result.get('analysis_summary', '')}\n"
            f"Top 推荐：{ai_result.get('top_picks', '')}\n"
            f"风险提示：{ai_result.get('risk_alerts', '')}\n"
            f"结论：{ai_result.get('conclusion', '')}"
        )
        if ai_result.get("analysis_summary", "").startswith("LLM"):
            logger.info("AI 核实跳过：LLM 未配置")
        else:
            logger.info(f"AI 核实完成：{ai_result.get('analysis_summary', '')[:80]}")
    except Exception as e:
        logger.warning(f"AI 核实失败（不影响推送）: {e}")
        ai_summary = ""

    # ── Step 5.5: Kronos 预测（可选，推送前填充，写入卡片与 LLM 视图）──
    if args.kronos:
        logger.info("🧠 启动 Kronos 预测...")
        attach_kronos(analyzed, args.date)

    # ── Step 5.6: 历史回测（可选，逐只信号回测，写入卡片）────────
    if args.backtest:
        logger.info(
            f"📈 启动历史回测（窗口 {args.backtest_days} 天，"
            f"horizon {args.backtest_horizon} 日）..."
        )
        attach_backtest(analyzed, args.date, args.backtest_days, args.backtest_horizon)

    # ── Step 5.7: 写出汇总 JSON（含 kronos / backtest 字段）────
    _write_intersection_json()

    # ── Step 6: 飞书推送（汇总 + 逐只）───────────────────────────
    if args.push:
        # 6a. 汇总卡片推送
        card = _build_combined_card(analyzed, args.date, source_sets)
        if ai_summary:
            card += f"\n\n---\n🤖 **AI 核实（多线共振视角）**\n{ai_summary}"
        ok = push_feishu("text", content=card, title=f"🔗 {args.date} 多线共振综合选股")
        logger.info(f"{'✅' if ok else '❌'} 飞书汇总推送{'成功' if ok else '失败'}")

        # 6b. 逐只推送（每只单独一张卡片，含技术面+基本面）
        if args.per_stock_push and analyzed:
            _push_per_stock(analyzed, args.date)
    else:
        logger.info("已跳过飞书推送（--push 0）")

    logger.info("=" * 60)
    per_push_info = f"，逐只推送 {len(analyzed)} 只" if (args.push and args.per_stock_push and analyzed) else ""
    logger.info(
        f"综合选股交集分析完成：交集 {len(cands)} 只，"
        f"技术分析 {len(analyzed)} 只{per_push_info}"
    )
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
