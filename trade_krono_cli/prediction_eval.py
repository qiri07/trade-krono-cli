"""
Prediction Evaluation — 预测评估模块。

从 ResearchDatabase 读取历史分析结果，
对照实际行情验证预测准确性，输出统计指标。

这是从 AI Demo → Quant System 的关键一步：
  • Kronos 方向准确率（5D / 10D / 20D）
  • TA BUY/HOLD/SELL 胜率与收益
  • TA vs Kronos vs TA+Kronos 三者对比
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import pandas as pd
from loguru import logger

from trade_krono_cli.cache import get_research
from trade_krono_cli.data import fetch_kline
from trade_krono_cli.security import validate_ticker, validate_date


# ═══════════════════════════════════════════════════════
# 核心：获取实际价格
# ═══════════════════════════════════════════════════════

def _get_close_price(ticker: str, date_str: str) -> Optional[float]:
    """获取指定日期的收盘价（支持精确日期和最近交易日）。"""
    try:
        ticker = validate_ticker(ticker)
        date_str = validate_date(date_str)
        # 拉取当天及前后各 3 天的 K 线，找到最近的收盘价
        start = (datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=5)).strftime("%Y-%m-%d")
        end = (datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=5)).strftime("%Y-%m-%d")
        df = fetch_kline(ticker, start, end, frequency="d", use_cache=True)
        if df.empty:
            return None
        # 找最接近目标日期的记录
        df["date_col"] = pd.to_datetime(df["timestamps"]).dt.strftime("%Y-%m-%d")
        target = df[df["date_col"] == date_str]
        if not target.empty:
            return float(target["close"].iloc[0])
        # fallback: 取最近的收盘价
        df_sorted = df.sort_values("timestamps", ascending=False)
        return float(df_sorted["close"].iloc[0])
    except Exception as e:
        logger.debug(f"获取收盘价失败 {ticker} @ {date_str}: {e}")
        return None


def _calc_return(entry_price: float, exit_price: float) -> float:
    """计算收益率（%）。"""
    if entry_price <= 0 or exit_price <= 0:
        return 0.0
    return (exit_price - entry_price) / entry_price * 100.0


# ═══════════════════════════════════════════════════════
# 预测评估结果
# ═══════════════════════════════════════════════════════

@dataclass
class EvalRecord:
    """单次预测的评估记录。"""
    ticker: str
    eval_date: str
    horizon_days: int
    pred_direction: Optional[str]   # UP / DOWN / FLAT
    pred_return_pct: Optional[float]
    actual_return_pct: float
    actual_direction: str           # UP / DOWN / FLAT
    is_direction_correct: bool      # 方向是否预测正确
    error_pct: float                # 预测误差 = 预测 - 实际
    # 附加上下文（用于分组统计）
    ta_signal: Optional[str] = None
    composite_score: Optional[float] = None


@dataclass
class HorizonMetrics:
    """指标汇总按单一 horizon（天）分组。"""
    kronos_dir_accuracy: float = 0.0
    ta_buy_win_rate: float = 0.0
    ta_buy_avg_return: float = 0.0
    ta_hold_avg_return: float = 0.0
    combined_buy_up_win_rate: float = 0.0
    combined_buy_up_avg_return: float = 0.0
    high_conf_win_rate: float = 0.0
    high_conf_avg_return: float = 0.0


@dataclass
class EvaluationSummary:
    """评估汇总统计。"""
    # 聚合计数
    kronos_n: int = 0
    ta_buy_n: int = 0
    ta_hold_n: int = 0
    combined_buy_up_n: int = 0
    high_conf_n: int = 0
    # 按 horizon 分组的指标
    horizons: dict[int, HorizonMetrics] = field(default_factory=dict)
    records: list[EvalRecord] = field(default_factory=list)


# ═══════════════════════════════════════════════════════
# 评估器
# ═══════════════════════════════════════════════════════

class PredictionEvaluator:
    """
    历史预测评估器。

    工作流程：
      1. 从 ResearchDatabase 读取历史 jobs + signals
      2. 对每个信号，获取实际价格并计算 realized return
      3. 按 horizon (5/10/20 天) 分组统计
      4. 输出评估报告
    """

    HORIZONS = [5, 10, 20]

    def __init__(self, max_workers: int = 4):
        self._research = get_research()
        self._max_workers = max_workers

    def evaluate(
        self,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        tickers: Optional[list[str]] = None,
        store: bool = True,
    ) -> EvaluationSummary:
        """
        执行预测评估。

        Parameters
        ----------
        from_date : str, optional
            起始分析日期（YYYY-MM-DD），默认从数据库最早记录开始
        to_date : str, optional
            截止分析日期，默认到今天
        tickers : list[str], optional
            只评估指定股票，默认全部
        store : bool
            是否将评估结果写入 research database

        Returns
        -------
        EvaluationSummary
        """
        logger.info("📊 开始预测评估...")
        t0 = time.time()

        # 1. 获取候选评估数据
        jobs = self._research.list_jobs(limit=1000)
        if from_date:
            jobs = [j for j in jobs if j["date"] >= from_date]
        if to_date:
            jobs = [j for j in jobs if j["date"] <= to_date]
        if not jobs:
            logger.warning("⚠️  没有可评估的历史作业")
            return EvaluationSummary()

        # 2. 收集所有需要评估的信号
        records_to_eval: list[tuple[str, str, str, Optional[str], Optional[float],
                                     Optional[str], Optional[float], Optional[float]]] = []
        # (job_id, ticker, eval_date, ta_signal, kronos_direction,
        #  composite_score, kronos_change_pct, ta_confidence)

        for job in jobs:
            signals = self._research.get_signals_by_job(job["job_id"])
            for sig in signals:
                if sig.get("ta_error") and sig.get("kronos_error"):
                    continue  # 双源都失败，跳过
                if tickers and sig["ticker"] not in tickers:
                    continue
                records_to_eval.append((
                    job["job_id"],
                    sig["ticker"],
                    job["date"],
                    sig.get("ta_signal"),
                    sig.get("kronos_direction"),
                    sig.get("composite_score"),
                    sig.get("kronos_change"),
                    sig.get("ta_confidence"),
                ))

        if not records_to_eval:
            logger.warning("⚠️  没有可评估的信号")
            return EvaluationSummary()

        logger.info(f"📋 共 {len(records_to_eval)} 条信号待评估")

        # 3. 逐条获取实际价格并计算 realized return
        eval_records: list[EvalRecord] = []
        for i, (job_id, ticker, eval_date, ta_signal,
                kronos_dir, comp_score, kronos_chg, ta_conf) in enumerate(
                records_to_eval, 1):
            entry_price = _get_close_price(ticker, eval_date)
            if entry_price is None or entry_price <= 0:
                logger.debug(f"  ⏭️ 跳过 {ticker} @ {eval_date}: 无入口价格")
                continue

            for horizon in self.HORIZONS:
                eval_date_h = (
                    datetime.strptime(eval_date, "%Y-%m-%d")
                    + timedelta(days=horizon)
                ).strftime("%Y-%m-%d")
                exit_price = _get_close_price(ticker, eval_date_h)

                if exit_price is None or exit_price <= 0:
                    continue

                actual_return = _calc_return(entry_price, exit_price)
                actual_dir = "UP" if actual_return > 1.0 else ("DOWN" if actual_return < -1.0 else "FLAT")

                pred_dir = str(kronos_dir) if kronos_dir is not None else None  # type: ignore[arg-type]
                pred_ret = kronos_chg  # Kronos 预测涨跌幅

                is_dir_correct = False
                if pred_dir and pred_dir != "FLAT":
                    is_dir_correct = (pred_dir == actual_dir)

                error = (pred_ret - actual_return) if pred_ret is not None else 0.0

                eval_records.append(EvalRecord(
                    ticker=ticker,
                    eval_date=eval_date,
                    horizon_days=horizon,
                    pred_direction=pred_dir,
                    pred_return_pct=pred_ret,
                    actual_return_pct=round(actual_return, 4),
                    actual_direction=actual_dir,
                    is_direction_correct=is_dir_correct,
                    error_pct=round(error, 4),
                ))

            if i % 20 == 0:
                logger.info(f"  进度: {i}/{len(records_to_eval)}")

        elapsed = time.time() - t0
        logger.info(f"✅ 评估完成: {len(eval_records)} 条记录, 耗时 {elapsed:.1f}s")

        # 4. 计算统计
        summary = self._compute_summary(eval_records)

        # 5. 存储到 research DB
        if store:
            self._store_summary(summary, jobs[0]["date"] if jobs else None)

        return summary

    def _compute_summary(self, records: list[EvalRecord]) -> EvaluationSummary:
        """根据评估记录计算汇总统计。"""
        summary = EvaluationSummary(records=records)

        for horizon in self.HORIZONS:
            h_records = [r for r in records if r.horizon_days == horizon]
            if not h_records:
                continue

            metrics = HorizonMetrics()

            # ── Kronos 方向准确率 ──────────────────────────────
            kronos_records = [r for r in h_records
                              if r.pred_direction is not None]
            if kronos_records:
                correct = sum(1 for r in kronos_records if r.is_direction_correct)
                acc = correct / len(kronos_records) * 100
                metrics.kronos_dir_accuracy = round(acc, 1)
                summary.kronos_n += len(kronos_records)

            # ── TA BUY 胜率 ────────────────────────────────────
            buy_records = [r for r in h_records if r.ta_signal == "BUY"]
            if buy_records:
                wins = sum(1 for r in buy_records if r.actual_return_pct > 0)
                avg_ret = sum(r.actual_return_pct for r in buy_records) / len(buy_records)
                metrics.ta_buy_win_rate = round(wins / len(buy_records) * 100, 1)
                metrics.ta_buy_avg_return = round(avg_ret, 2)
                summary.ta_buy_n += len(buy_records)

            # ── TA HOLD 平均收益 ───────────────────────────────
            hold_records = [r for r in h_records if r.ta_signal == "HOLD"]
            if hold_records:
                avg_ret = sum(r.actual_return_pct for r in hold_records) / len(hold_records)
                metrics.ta_hold_avg_return = round(avg_ret, 2)
                summary.ta_hold_n += len(hold_records)

            # ── 综合信号（TA BUY + Kronos UP）──────────────────
            combined_records = [r for r in h_records
                                if r.ta_signal == "BUY"
                                and r.pred_direction == "UP"]
            if combined_records:
                wins = sum(1 for r in combined_records if r.actual_return_pct > 0)
                avg_ret = sum(r.actual_return_pct for r in combined_records) / len(combined_records)
                metrics.combined_buy_up_win_rate = round(wins / len(combined_records) * 100, 1)
                metrics.combined_buy_up_avg_return = round(avg_ret, 2)
                summary.combined_buy_up_n += len(combined_records)

            # ── 高置信信号（composite_score >= 70）─────────────
            high_conf_records = [r for r in h_records
                                 if r.composite_score is not None
                                 and r.composite_score >= 70]
            if high_conf_records:
                wins = sum(1 for r in high_conf_records if r.actual_return_pct > 0)
                avg_ret = sum(r.actual_return_pct for r in high_conf_records) / len(high_conf_records)
                metrics.high_conf_win_rate = round(wins / len(high_conf_records) * 100, 1)
                metrics.high_conf_avg_return = round(avg_ret, 2)
                summary.high_conf_n += len(high_conf_records)

            summary.horizons[horizon] = metrics

        return summary

    def _store_summary(
        self, summary: EvaluationSummary, eval_date_range: Optional[str],
    ) -> None:
        """将评估结果存储到 research database。"""
        import sqlite3
        from trade_krono_cli.config import get_settings

        db_path = self._research._db_path
        with sqlite3.connect(db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS evaluation_results (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    eval_at         REAL NOT NULL,
                    eval_date_range TEXT,
                    n_records       INTEGER NOT NULL,
                    kronos_acc_5d   REAL,
                    kronos_acc_10d  REAL,
                    kronos_acc_20d  REAL,
                    ta_buy_wr_5d    REAL,
                    ta_buy_wr_10d   REAL,
                    ta_buy_wr_20d   REAL,
                    combined_wr_5d  REAL,
                    combined_wr_10d REAL,
                    combined_wr_20d REAL,
                    high_conf_wr_5d REAL,
                    high_conf_wr_10d REAL,
                    summary_json    TEXT NOT NULL
                )
            """)
            summary_json = json.dumps({
                "kronos_n": summary.kronos_n,
                "kronos_dir_accuracy": {
                    str(h): m.kronos_dir_accuracy
                    for h, m in summary.horizons.items()
                },
                "ta_buy_win_rate": {
                    str(h): m.ta_buy_win_rate
                    for h, m in summary.horizons.items()
                },
                "ta_buy_avg_return": {
                    str(h): m.ta_buy_avg_return
                    for h, m in summary.horizons.items()
                },
                "combined_buy_up_win_rate": {
                    str(h): m.combined_buy_up_win_rate
                    for h, m in summary.horizons.items()
                },
                "combined_buy_up_avg_return": {
                    str(h): m.combined_buy_up_avg_return
                    for h, m in summary.horizons.items()
                },
                "high_conf_win_rate": {
                    str(h): m.high_conf_win_rate
                    for h, m in summary.horizons.items()
                },
                "high_conf_avg_return": {
                    str(h): m.high_conf_avg_return
                    for h, m in summary.horizons.items()
                },
            }, ensure_ascii=False)

            conn.execute(
                """INSERT INTO evaluation_results
                   (eval_at, eval_date_range, n_records,
                    kronos_acc_5d, kronos_acc_10d, kronos_acc_20d,
                    ta_buy_wr_5d, ta_buy_wr_10d, ta_buy_wr_20d,
                    combined_wr_5d, combined_wr_10d, combined_wr_20d,
                    high_conf_wr_5d, high_conf_wr_10d, summary_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    time.time(), eval_date_range,
                    len(summary.records),
                    summary.horizons.get(5, HorizonMetrics()).kronos_dir_accuracy,
                    summary.horizons.get(10, HorizonMetrics()).kronos_dir_accuracy,
                    summary.horizons.get(20, HorizonMetrics()).kronos_dir_accuracy,
                    summary.horizons.get(5, HorizonMetrics()).ta_buy_win_rate,
                    summary.horizons.get(10, HorizonMetrics()).ta_buy_win_rate,
                    summary.horizons.get(20, HorizonMetrics()).ta_buy_win_rate,
                    summary.horizons.get(5, HorizonMetrics()).combined_buy_up_win_rate,
                    summary.horizons.get(10, HorizonMetrics()).combined_buy_up_win_rate,
                    summary.horizons.get(20, HorizonMetrics()).combined_buy_up_win_rate,
                    summary.horizons.get(5, HorizonMetrics()).high_conf_win_rate,
                    summary.horizons.get(10, HorizonMetrics()).high_conf_win_rate,
                    summary_json,
                ),
            )
            conn.commit()
        logger.info("💾 评估结果已存储到研究数据库")

    def get_latest_evaluation(self) -> Optional[dict]:
        """获取最新的评估结果。"""
        import sqlite3
        db_path = self._research._db_path
        try:
            with sqlite3.connect(db_path) as conn:
                row = conn.execute(
                    "SELECT id, eval_at, eval_date_range, n_records, summary_json "
                    "FROM evaluation_results ORDER BY eval_at DESC LIMIT 1"
                ).fetchone()
        except sqlite3.OperationalError:
            return None
        if not row:
            return None
        return {
            "id": row[0],
            "eval_at": row[1],
            "eval_date_range": row[2],
            "n_records": row[3],
            "summary": json.loads(row[4]),
        }

    def print_report(self, summary: EvaluationSummary) -> None:
        """打印评估报告到控制台。"""
        print()
        print("=" * 60)
        print("  📊 预测评估报告")
        print("=" * 60)
        print()

        # ── Kronos 方向准确率 ──────────────────────────────────
        print("┌─ Kronos 方向准确率 ─────────────────────────────────┐")
        print(f"│  样本数: {summary.kronos_n}                              │")
        for h in self.HORIZONS:
            m = summary.horizons.get(h)
            acc = m.kronos_dir_accuracy if m else 0.0
            marker = "✅" if acc > 55 else "⚠️" if acc > 50 else "❌"
            print(f"│  {marker} {h}D 准确率: {acc:5.1f}%                       │")
        print("└" + "─" * 58 + "┘")
        print()

        # ── TA BUY 胜率 ───────────────────────────────────────
        print("┌─ TA BUY 信号表现 ───────────────────────────────────┐")
        print(f"│  样本数: {summary.ta_buy_n}                              │")
        for h in self.HORIZONS:
            m = summary.horizons.get(h)
            wr = m.ta_buy_win_rate if m else 0.0
            avg_ret = m.ta_buy_avg_return if m else 0.0
            marker = "✅" if wr > 55 else "⚠️" if wr > 50 else "❌"
            print(f"│  {marker} {h}D 胜率: {wr:5.1f}%  "
                  f"平均收益: {avg_ret:+.2f}%                    │")
        print("└" + "─" * 58 + "┘")
        print()

        # ── 综合信号（TA BUY + Kronos UP）────────────────────
        print("┌─ 综合信号（TA BUY + Kronos UP）─────────────────────┐")
        print(f"│  样本数: {summary.combined_buy_up_n}                          │")
        for h in self.HORIZONS:
            m = summary.horizons.get(h)
            wr = m.combined_buy_up_win_rate if m else 0.0
            avg_ret = m.combined_buy_up_avg_return if m else 0.0
            marker = "✅" if wr > 60 else "⚠️" if wr > 55 else "❌"
            print(f"│  {marker} {h}D 胜率: {wr:5.1f}%  "
                  f"平均收益: {avg_ret:+.2f}%                    │")
        print("└" + "─" * 58 + "┘")
        print()

        # ── 高置信信号（composite_score >= 70）───────────────
        print("┌─ 高置信信号（综合分 ≥ 70）──────────────────────────┐")
        print(f"│  样本数: {summary.high_conf_n}                              │")
        for h in self.HORIZONS:
            m = summary.horizons.get(h)
            wr = m.high_conf_win_rate if m else 0.0
            avg_ret = m.high_conf_avg_return if m else 0.0
            marker = "✅" if wr > 60 else "⚠️" if wr > 55 else "❌"
            print(f"│  {marker} {h}D 胜率: {wr:5.1f}%  "
                  f"平均收益: {avg_ret:+.2f}%                    │")
        print("└" + "─" * 58 + "┘")
        print()

        # ── 基准：随机基准 ────────────────────────────────────
        print("┌─ 基准对比（50% 随机基准）───────────────────────────┐")
        print("│  方向准确率 > 50% = 超越随机                          │")
        print("│  胜率 > 50% = 正向 alpha                              │")
        print("└" + "─" * 58 + "┘")
        print()


# ═══════════════════════════════════════════════════════
# CLI 入口
# ═══════════════════════════════════════════════════════

def run_evaluation(
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    tickers: Optional[list[str]] = None,
    latest: bool = False,
) -> None:
    """执行预测评估并打印报告。"""
    evaluator = PredictionEvaluator()

    if latest:
        result = evaluator.get_latest_evaluation()
        if not result:
            print("⚠️  暂无评估结果，请先运行完整评估")
            return
        print()
        print("=" * 60)
        print("  📊 最新评估结果")
        print("=" * 60)
        print(f"  评估时间: {datetime.fromtimestamp(result['eval_at']).strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"  评估日期范围: {result['eval_date_range'] or '全部'}")
        print(f"  评估记录数: {result['n_records']}")
        print()
        summary = result["summary"]

        # Kronos
        print("┌─ Kronos 方向准确率 ─────────────────────────────────┐")
        print(f"│  样本数: {summary.get('kronos_n', 0)}                              │")
        for h in [5, 10, 20]:
            acc = summary.get("kronos_dir_accuracy", {}).get(str(h), 0)
            marker = "✅" if acc > 55 else "⚠️" if acc > 50 else "❌"
            print(f"│  {marker} {h}D 准确率: {acc:5.1f}%                       │")
        print("└" + "─" * 58 + "┘")
        print()

        # TA BUY
        print("┌─ TA BUY 信号表现 ───────────────────────────────────┐")
        ta_buy_n = sum(1 for r in summary.records if r.ta_signal == "BUY")
        print(f"│  样本数: {ta_buy_n}                             │")
        for h in [5, 10, 20]:
            wr = summary.get("ta_buy_win_rate", {}).get(str(h), 0)
            avg_ret = summary.get("ta_buy_avg_return", {}).get(str(h), 0)
            marker = "✅" if wr > 55 else "⚠️" if wr > 50 else "❌"
            print(f"│  {marker} {h}D 胜率: {wr:5.1f}%  "
                  f"平均收益: {avg_ret:+.2f}%                    │")
        print("└" + "─" * 58 + "┘")
        print()

        # Combined
        print("┌─ 综合信号（TA BUY + Kronos UP）─────────────────────┐")
        combined_n = sum(1 for r in summary.records if r.ta_signal == "BUY" and r.pred_direction == "UP")
        print(f"│  样本数: {combined_n}                          │")
        for h in [5, 10, 20]:
            wr = summary.get("combined_buy_up_win_rate", {}).get(str(h), 0)
            avg_ret = summary.get("combined_buy_up_avg_return", {}).get(str(h), 0)
            marker = "✅" if wr > 60 else "⚠️" if wr > 55 else "❌"
            print(f"│  {marker} {h}D 胜率: {wr:5.1f}%  "
                  f"平均收益: {avg_ret:+.2f}%                    │")
        print("└" + "─" * 58 + "┘")
        print()

        return

    # 完整评估
    summary = evaluator.evaluate(
        from_date=from_date,
        to_date=to_date,
        tickers=tickers,
        store=True,
    )
    evaluator.print_report(summary)
