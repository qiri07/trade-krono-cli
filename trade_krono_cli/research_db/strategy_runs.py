"""研究数据库 — Strategy Runs 表读写。"""

from __future__ import annotations

import json

from trade_krono_cli.research_db.base import ResearchDatabase


class StrategyRunsMixin(ResearchDatabase):
    """Strategy Runs 表相关方法。"""

    def insert_strategy_run(
        self,
        run_at: float,
        strategy: str,
        params: dict,
        tickers: list[str],
        results: list[dict],
        notes: str | None = None,
        config_hash: str | None = None,
    ) -> int:
        """记录一次评分策略运行结果到 strategy_runs 表。

        Parameters
        ----------
        run_at      : 运行时间戳（epoch seconds）
        strategy    : 策略名称，如 "linear" / "multiplicative"
        params      : 策略参数 dict（JSON 序列化）
        tickers     : 本次运行涉及的股票代码列表
        results     : 合并结果列表，每项含 ticker + composite_score
        notes       : 备注（可选）
        config_hash : 配置哈希（可选）

        Returns
        -------
        int : 插入的行 ID

        """
        with self._conn as conn:
            cursor = conn.execute(
                "INSERT INTO strategy_runs "
                "(run_at, strategy, params, tickers, results, notes, config_hash) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    run_at,
                    strategy,
                    json.dumps(params, ensure_ascii=False),
                    json.dumps(tickers, ensure_ascii=False),
                    json.dumps(results, ensure_ascii=False, default=str),
                    notes,
                    config_hash,
                ),
            )
            conn.commit()
            return int(cursor.lastrowid or 0)

    def query_strategy_history(
        self,
        strategy: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """查询评分策略历史运行记录。

        Parameters
        ----------
        strategy : 筛选特定策略（None = 查全部）
        limit    : 最多返回条数

        Returns
        -------
        list[dict] : 按 run_at 降序排列的历史记录

        """
        with self._conn as conn:
            if strategy:
                rows = conn.execute(
                    "SELECT run_at, strategy, params, tickers, results, "
                    "       notes, config_hash "
                    "FROM strategy_runs "
                    "WHERE strategy = ? "
                    "ORDER BY run_at DESC LIMIT ?",
                    (strategy, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT run_at, strategy, params, tickers, results, "
                    "       notes, config_hash "
                    "FROM strategy_runs "
                    "ORDER BY run_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [
            {
                "run_at": r[0],
                "strategy": r[1],
                "params": json.loads(r[2]) if r[2] else {},
                "tickers": json.loads(r[3]) if r[3] else [],
                "n_results": len(json.loads(r[4])) if r[4] else 0,
                "avg_score": self._safe_avg_score(r[4]),
                "notes": r[5],
                "config_hash": r[6],
            }
            for r in rows
        ]

    @staticmethod
    def _safe_avg_score(results_json: str | None) -> float | None:
        """从 JSON 字符串中提取平均 composite_score，非法时返回 None。"""
        if not results_json:
            return None
        try:
            results = json.loads(results_json)
            scores = [  # type: ignore[assignment]
                float(r.get("composite_score") or 0)
                for r in results
                if isinstance(r, dict) and r.get("composite_score") is not None
            ]
            if not scores:
                return None
            return round(sum(scores) / len(scores), 2)
        except (ValueError, TypeError):
            return None
