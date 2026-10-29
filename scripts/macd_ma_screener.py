#!/usr/bin/env python3
"""MACD+均线 A 股筛选器（第 10 步）。

筛选条件：
  - 日 MA5 > MA13
  - 日 MACD DIF > 0（0 轴之上）
  - 剔除 ST 股
  - 剔除昨日涨停股（主板 ≥9.95%，创业板/科创板 ≥19.95%）
  - 剔除小市值（总市值 < 50 亿）
  - 剔除 PE 异常（PE_TTM ≤ 0 或 ≥ 30）
  - 排除北交所 / 港股 / ETF

用法：
  uv run python scripts/macd_ma_screener.py                      # 默认今日
  uv run python scripts/macd_ma_screener.py --date 2026-10-27    # 指定日期
  uv run python scripts/macd_ma_screener.py --push 0             # 跳过飞书推送

输出：outputs/results/macd_ma_screener_{YYYYMMDD}.json
结果被 scripts/combined_stock_analysis.py 作为 "macd_ma" 来源线汇集进第 9 步交集。
"""

from __future__ import annotations

import argparse
import json
import pickle
import sqlite3
import subprocess
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
from loguru import logger

from scripts._utils import (
    ST_NAME_KEYWORDS_MINIMAL,
    get_cache_db_path,
    is_etf_ticker,
    normalize_ticker_key,
    ticker_to_thscode,
)

# ── 常量 ──────────────────────────────────────────────────────────────────────
_RESULTS = Path("outputs/results")
_META_FILE = Path("outputs/cache/metadata.json")

# baostock 总股本缓存表（ticker + 季度 → 总股本万股）
_SHARE_CAP_TABLE = "share_cap_cache"

# 默认取最近 4 个报告期（按新到旧）
def _recent_quarters(anchor_date: str) -> list[str]:
    """生成以 anchor_date 为基准的最近 4 个财务报告期（YYYY-N 格式）。"""
    year = int(anchor_date[:4])
    month = int(anchor_date[5:7])
    quarter = (month - 1) // 3 + 1
    seq: list[tuple[int, int]] = []
    y, q = year, quarter
    for _ in range(4):
        seq.append((y, q))
        q -= 1
        if q == 0:
            q = 4
            y -= 1
    return [f"{yy}-{qq}" for yy, qq in seq]


def _limit_pct(bare_code: str) -> float:
    """涨停幅度（%）：创业板(30)/科创板(68) 20%，主板 10%。"""
    return 20.0 if bare_code[:2] in ("30", "68") else 10.0


@dataclass
class MacdMaResult:
    """MACD+均线筛选结果（每只通过股票一条记录）。"""

    ticker: str
    name: str
    close: float
    ma5: float
    ma13: float
    macd: float
    daily_chg_pct: float
    pe_ttm: float | None = None
    market_cap: float | None = None

    def to_dict(self) -> dict[str, object]:
        """转 JSON 可序列化的 dict（与 combined_stock_analysis 读取格式对齐）。"""
        return {
            "ticker": self.ticker,
            "name": self.name,
            "close": self.close,
            "ma5": self.ma5,
            "ma13": self.ma13,
            "macd": self.macd,
            "daily_chg_pct": self.daily_chg_pct,
            "pe_ttm": self.pe_ttm,
            "market_cap": self.market_cap,
        }


# ── 技术面计算 ─────────────────────────────────────────────────────────────────
def calc_macd_dif(closes: pd.Series, fast: int = 12, slow: int = 26) -> float | None:
    """MACD DIF 值（>0 表示 0 轴之上）。数据不足返回 None。

    slow+9 为 EMA 预热保守门槛（比 multi_line_resonance._macd 的 slow+2 更严格，
    避免前 26 个样本 EMA 预热不足导致 DIF 偏差）。
    """
    if len(closes) < slow + 9:
        return None
    ema_fast = closes.ewm(span=fast, adjust=False).mean()
    ema_slow = closes.ewm(span=slow, adjust=False).mean()
    return float((ema_fast - ema_slow).iloc[-1])


def _fetch_all_stock_names(date_str: str) -> dict[str, str]:
    """baostock query_all_stock 一次性拉全市场名称（失败降级为空映射）。"""
    names: dict[str, str] = {}
    try:
        import baostock as bs

        bs.login()
        try:
            rs = bs.query_all_stock(date_str)
            while rs.next():
                row = rs.get_row_data()
                code, _status, code_name = row[0], row[1], row[2]
                bare = code.split(".")[-1] if "." in code else code
                if code_name:
                    names[bare] = code_name.strip()
        finally:
            bs.logout()
    except Exception as e:
        logger.warning(f"⚠️ baostock 名称查询失败，降级使用元数据名称: {str(e)[:80]}")
    return names


# ── 市值估算 ───────────────────────────────────────────────────────────────────
def _init_share_cap_cache(db_path: Path) -> None:
    """确保总股本缓存表存在。"""
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        f"CREATE TABLE IF NOT EXISTS {_SHARE_CAP_TABLE} ("
        "ticker TEXT NOT NULL, quarter TEXT NOT NULL, total_share_wan REAL,"
        "updated_at TEXT DEFAULT (datetime('now')),"
        "PRIMARY KEY (ticker, quarter))"
    )
    conn.commit()
    conn.close()


def load_market_cap(
    results: list[dict[str, object]], cache_db: Path, quarters: list[str]
) -> int:
    """baostock query_profit_data.totalShare + 收盘价估算总市值（亿元）。

    总股本（万股）× 最新收盘价 ÷ 1e8 = 亿元。结果缓存到 share_cap_cache 表。
    返回成功获取股本的只数。
    """
    import baostock as bs

    _init_share_cap_cache(cache_db)

    # 读缓存
    conn = sqlite3.connect(str(cache_db))
    cached: dict[str, dict[str, float]] = {}
    for tk, q, val in conn.execute(f"SELECT ticker, quarter, total_share_wan FROM {_SHARE_CAP_TABLE}"):
        if val and val > 0:
            cached.setdefault(tk, {})[q] = val
    conn.close()

    # 确定每只需查的报告期
    need_fetch: set[str] = set()
    total_shares_map: dict[str, float] = {}
    for r in results:
        tk = str(r["ticker"])
        for q in quarters:
            if q in cached.get(tk, {}):
                total_shares_map[tk] = cached[tk][q]
                break
        else:
            need_fetch.add(tk)

    fetched: dict[str, float] = {}
    fetched_quarter: dict[str, str] = {}
    if need_fetch:
        remaining: set[str] = set(need_fetch)
        for attempt in range(3):
            if not remaining:
                break
            try:
                bs.login()
                try:
                    for tk in sorted(remaining):
                        got = False
                        for year_q in quarters:
                            year, quarter = year_q.split("-")
                            try:
                                rs = bs.query_profit_data(code=tk, year=year, quarter=quarter)
                                while rs.next():
                                    row = rs.get_row_data()
                                    val = float(row[9]) if row[9] else 0.0
                                    if val > 0:
                                        fetched[tk] = val
                                        fetched_quarter[tk] = year_q
                                        got = True
                                        break
                            except Exception:
                                continue
                            if got:
                                break
                        if got:
                            remaining.discard(tk)
                finally:
                    bs.logout()
            except Exception as e:
                logger.warning(f"⚠️ baostock 总股本查询中断（第 {attempt + 1} 轮，登录/退出）: {str(e)[:80]}")
            if remaining and attempt < 2:
                logger.info(f"  第 {attempt + 1} 轮后仍缺 {len(remaining)} 只股本，30s 后重试")
                time.sleep(30)
        if remaining:
            logger.warning(f"  股本最终缺失 {len(remaining)} 只（市值视为 None 剔除）")

    # 写回缓存
    if fetched:
        wconn = sqlite3.connect(str(cache_db))
        for tk, val in fetched.items():
            wconn.execute(
                f"INSERT OR REPLACE INTO {_SHARE_CAP_TABLE} (ticker, quarter, total_share_wan) "
                "VALUES (?, ?, ?)",
                (tk, fetched_quarter.get(tk, quarters[0]), val),
            )
        wconn.commit()
        wconn.close()

    for tk, val in fetched.items():
        total_shares_map[tk] = val

    # 计算市值
    for r in results:
        tk = str(r["ticker"])
        ts_wan = total_shares_map.get(tk, 0.0)
        close_raw = r.get("close")
        close_val = float(close_raw) if isinstance(close_raw, (int, float)) else 0.0
        r["market_cap"] = round(ts_wan * close_val / 1e8, 1) if (ts_wan > 0 and close_val > 0) else None
    return len(fetched) + sum(1 for r in results if cached.get(str(r["ticker"])))


def apply_market_cap_filter(
    results: list[dict[str, object]], min_cap_yi: float = 50.0
) -> tuple[list[dict[str, object]], int]:
    """剔除总市值 < min_cap_yi 亿元的股票。市值缺失（None）同样剔除。"""
    kept: list[dict[str, object]] = []
    removed = 0
    for r in results:
        cap = r.get("market_cap")
        cap_num = float(cap) if isinstance(cap, (int, float)) else None
        if cap_num is None or cap_num < min_cap_yi:
            removed += 1
            continue
        kept.append(r)
    return kept, removed


# ── PE 补充（本地 metadata 缺失时走 Fuyao API 批量快照）────────────────────────
def _load_local_metadata() -> tuple[dict[str, dict[str, object]], dict[str, float]]:
    """读 outputs/cache/metadata.json，返回 (全量 meta, {ticker: pe_ttm})。"""
    if not _META_FILE.exists():
        return {}, {}
    meta = json.loads(_META_FILE.read_text(encoding="utf-8"))
    pe_map: dict[str, float] = {}
    for k, v in meta.items():
        if isinstance(v, dict):
            pe = v.get("pe_ttm")
            if pe is not None:
                pe_map[normalize_ticker_key(k)] = float(pe)
    return meta, pe_map


def _fetch_pe_from_fuyao(codes: list[str], batch_size: int = 50) -> dict[str, float]:
    """同花顺 Fuyao 估值快照批量补 PE_TTM（仅对本地 metadata 缺失的代码）。

    返回 {6位裸代码: pe_ttm}，与调用方 `pe_extra.get(bare_code)` 对齐。
    负/0 PE 丢弃（与 multi_line_resonance._fetch_pe_snapshot 一致）。
    """
    from os import getenv

    api_key = getenv("HITHINK_FINANCE_API_KEY", "") or getenv("FUYAO_API_KEY", "")
    if not api_key:
        logger.warning("HITHINK_FINANCE_API_KEY 未配置，跳过 PE 批量补充")
        return {}
    base = "https://fuyao.10jqka.com.cn"
    result: dict[str, float] = {}
    # 先按 codes 切片，再转 thscode（与 multi_line_resonance._fetch_pe_snapshot 对齐，避免 O(n²) split）
    for i in range(0, len(codes), batch_size):
        batch_tickers = codes[i : i + batch_size]
        ths_param = ",".join(ticker_to_thscode(c) for c in batch_tickers)
        url = f"{base}/api/a-share/valuations/snapshot?thscodes={ths_param}"
        try:
            proc = subprocess.run(
                [
                    "curl",
                    "-s",
                    "--max-time",
                    "10",
                    "-w",
                    "\n%{http_code}",
                    url,
                    "-H",
                    f"X-api-key: {api_key}",
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
            stdout = proc.stdout.strip()
            nl = stdout.rfind("\n")
            if nl < 0:
                continue
            body, http_code = stdout[:nl], int(stdout[nl + 1 :])
            if http_code != 200:
                continue
            data = json.loads(body)
            if data.get("code") != 0:
                continue
            for item in data.get("data", {}).get("item", []):
                thscode = item.get("thscode", "")
                code_key = thscode.split(".")[0] if "." in thscode else thscode
                pe_raw = item.get("pe_ttm")
                if pe_raw is None:
                    continue
                try:
                    pe_f = float(pe_raw)
                    if pe_f > 0:  # 负/0 PE 丢弃（与现有实现对齐）
                        result[code_key] = pe_f
                except (TypeError, ValueError):
                    pass
        except Exception as e:
            logger.debug(f"PE 快照批次 {i // batch_size + 1} 失败: {str(e)[:60]}")
    return result


# ── 核心扫描 ───────────────────────────────────────────────────────────────────
def _scan_all(
    db_path: Path,
    st_set: set[str],
    name_map: dict[str, str],
    date_str: str,
) -> tuple[list[dict[str, object]], dict[str, int]]:
    """全市场扫描：MA5>MA13 + MACD>0 + 剔 ST + 剔昨日涨停（以 date_str 为基准日）。

    K 线按 ``date_str`` 截断（仅保留 <= date_str 的 K 线），保证 MA/MACD/涨停
    判定都基于 ``date_str`` 当天而非缓存最后日期。

    Returns
    -------
    (技术面通过的候选列表, 各剔除项计数)
    """
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT ticker, data FROM kline_cache WHERE ticker NOT LIKE 'bj.%' AND ticker NOT LIKE 'hk.%'"
    ).fetchall()
    conn.close()

    results: list[dict[str, object]] = []
    skipped: dict[str, int] = defaultdict(int)
    for tk, data in rows:
        # 归一化 ticker（缓存里可能混有裸代码，统一成 sh./sz. 前缀形式）
        tk = normalize_ticker_key(tk)
        try:
            df = pickle.loads(data)
        except Exception:
            continue
        if df is None or not isinstance(df, pd.DataFrame) or len(df) < 35:
            skipped["short_history"] += 1
            continue
        bare = tk.split(".")[-1]
        if is_etf_ticker(tk):
            skipped["etf"] += 1
            continue
        df = df.copy()
        if not pd.api.types.is_datetime64_any_dtype(df["timestamps"]):
            df["timestamps"] = pd.to_datetime(df["timestamps"], errors="coerce")
        else:
            df["timestamps"] = pd.to_datetime(df["timestamps"])
        df = df.set_index("timestamps").sort_index()
        # 按 --date 截断：仅保留 date_str 当天及之前的 K 线
        if not df.index.empty:
            last_dt = df.index.max()
            if last_dt is not None and last_dt > pd.Timestamp(date_str):
                df = df[df.index <= pd.Timestamp(date_str)]
        close = df["close"].astype(float)
        if len(close) < 35 or close.isna().any() or (close <= 0).any():
            skipped["bad_close"] += 1
            continue

        # 1) 日 MA5 > MA13
        ma5 = float(close.rolling(5).mean().iloc[-1])
        ma13 = float(close.rolling(13).mean().iloc[-1])
        if not (ma5 > ma13):
            skipped["ma5_not_gt_ma13"] += 1
            continue

        # 2) MACD DIF > 0
        macd_val = calc_macd_dif(close)
        if macd_val is None or macd_val <= 0:
            skipped["macd_below_zero"] += 1
            continue

        # 3) 剔除 ST（元数据标记 or 名称关键词）
        if tk in st_set:
            skipped["st"] += 1
            continue
        name = name_map.get(bare, "")
        if any(kw in name for kw in ST_NAME_KEYWORDS_MINIMAL):
            skipped["st"] += 1
            continue

        # 4) 剔除昨日涨停（date_str 当日收盘 vs 前日收盘，幅度 >= 涨停 - 0.05%）
        chg = (close.iloc[-1] / close.iloc[-2] - 1) * 100
        if chg >= _limit_pct(bare) - 0.05:
            skipped["limit_up"] += 1
            continue

        results.append(
            {
                "ticker": tk,
                "name": name,
                "close": round(float(close.iloc[-1]), 2),
                "ma5": round(ma5, 2),
                "ma13": round(ma13, 2),
                "macd": round(macd_val, 4),
                "daily_chg_pct": round(chg, 2),
            }
        )
    return results, dict(skipped)


# ── 主流程 ─────────────────────────────────────────────────────────────────────
def run(
    date_str: str | None = None,
    push: int = 1,
    min_cap_yi: float = 50.0,
    top_n: int = 0,
) -> list[dict[str, object]]:
    """执行 MACD+均线筛选。返回通过全部条件的结果列表。"""
    date_str = date_str or datetime.now().strftime("%Y-%m-%d")
    logger.info("=" * 60)
    logger.info(f"MACD+均线 A 股筛选器启动  日期={date_str}")
    logger.info(f"  条件：MA5>MA13 + MACD DIF>0 + 剔ST + 剔昨日涨停 + 市值≥{min_cap_yi:.0f}亿 + PE∈(0,30)")
    logger.info("=" * 60)

    # 数据检查
    from scripts._utils import check_data_freshness

    logger.info(f"[数据检查] {check_data_freshness(logger.info)}")

    db_path = get_cache_db_path()
    quarters = _recent_quarters(date_str)

    # 本地 metadata（ST 标记 + PE）
    meta, pe_local = _load_local_metadata()
    st_set = {
        normalize_ticker_key(k) for k, v in meta.items() if isinstance(v, dict) and v.get("is_st")
    }
    # 名称映射：baostock 全表（一次拉取，避免逐只查询）
    name_map = _fetch_all_stock_names(date_str)
    if not name_map:
        # 降级：用 metadata 里的名称
        for k, v in meta.items():
            if isinstance(v, dict) and v.get("name"):
                name_map[normalize_ticker_key(k).split(".")[-1]] = str(v["name"])
        logger.info("名称映射降级使用 metadata 名称")

    # 技术面扫描（按 date_str 截断 K 线，保证判定基于 --date 而非缓存最后日期）
    candidates, skipped = _scan_all(db_path, st_set, name_map, date_str)
    logger.info(f"📊 技术面通过 {len(candidates)} 只，剔除明细：{skipped}")
    if not candidates:
        logger.warning("无技术面通过的股票，直接退出")
        _write_result(date_str, [], top_n)
        return []

    # 名称补全（结果里的 name 若空则补）
    for r in candidates:
        if not r.get("name"):
            r["name"] = name_map.get(str(r["ticker"]).split(".")[-1], "")

    # PE 过滤：本地 metadata 优先，缺失走 Fuyao 批量补
    missing_pe = [str(r["ticker"]) for r in candidates if r["ticker"] not in pe_local]
    pe_extra: dict[str, float] = {}
    if missing_pe:
        logger.info(f"📊 本地 PE 缺失 {len(missing_pe)} 只，Fuyao API 批量补充...")
        pe_extra = _fetch_pe_from_fuyao(missing_pe)
        logger.info(f"  PE 补充 {len(pe_extra)}/{len(missing_pe)} 只")
    for r in candidates:
        ticker_str = str(r.get("ticker", ""))
        r["pe_ttm"] = pe_local.get(ticker_str, pe_extra.get(ticker_str.split(".")[-1]))

    # PE ∈ (0, 30)
    pe_kept: list[dict[str, object]] = []
    removed_pe = 0
    for r in candidates:
        pe = r.get("pe_ttm")
        pe_val: float = 999.0 if isinstance(pe, float | int) else -1.0
        if pe_val <= 0 or pe_val >= 30:
            removed_pe += 1
            continue
        pe_kept.append(r)
    logger.info(f"📊 PE 过滤(0,30)：剔除 {removed_pe} 只，剩余 {len(pe_kept)} 只")

    # 市值估算 + <50亿 剔除
    if pe_kept:
        load_market_cap(pe_kept, db_path, quarters)
        cap_kept, cap_removed = apply_market_cap_filter(pe_kept, min_cap_yi)
        logger.info(f"📊 市值过滤(≥{min_cap_yi:.0f}亿)：剔除 {cap_removed} 只，剩余 {len(cap_kept)} 只")
    else:
        cap_kept, cap_removed = [], 0

    results = cap_kept

    def _pe_sort_key(item: dict[str, object]) -> float:
        pe = item.get("pe_ttm")
        return float(pe) if isinstance(pe, float | int) else 999.0

    results.sort(key=_pe_sort_key)
    if top_n > 0:
        results = results[:top_n]

    _write_result(date_str, results, top_n)
    logger.info(f"✅ 筛选完成：{len(results)} 只通过全部条件")

    # 飞书汇总推送
    if push and results:
        _push_feishu(date_str, results, skipped, cap_removed, removed_pe)
    elif push and not results:
        logger.info("无结果，跳过飞书推送")
    return results


def _write_result(date_str: str, results: list[dict[str, object]], top_n: int) -> None:
    """结果写 JSON 落盘（第 9 步 combined_stock_analysis 读取的来源文件）。"""
    date_compact = date_str.replace("-", "")
    out = _RESULTS / f"macd_ma_screener_{date_compact}.json"
    _RESULTS.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"💾 结果已保存: {out}")


def _push_feishu(
    date_str: str,
    results: list[dict[str, object]],
    skipped: dict[str, int],
    cap_removed: int,
    pe_removed: int,
) -> None:
    """飞书汇总推送。"""
    from scripts.feishu_utils import push_feishu

    lines = [f"📈 **{date_str} MACD+均线选股**\n"]
    lines.append(f"共 **{len(results)}** 只符合条件（MA5>MA13 + MACD>0 + 非ST + 非涨停 + 市值≥50亿 + PE∈(0,30)）")
    lines.append("")
    for r in results[:30]:
        cap = r.get("market_cap")
        cap_str = f"{cap:.0f}亿" if isinstance(cap, float | int) else "N/A"
        lines.append(
            f"  {str(r.get('ticker', ''))[:7]} {str(r.get('name', ''))[:8]:<8} "
            f"PE={r.get('pe_ttm')} 市值={cap_str} MACD={r.get('macd')}"
        )
    if len(results) > 30:
        lines.append(f"  ... 共 {len(results)} 只")
    ok = push_feishu("text", content="\n".join(lines), title=f"{date_str} MACD+均线选股")
    logger.info(f"{'✅' if ok else '❌'} 飞书汇总推送{'成功' if ok else '失败'}")


def main() -> None:
    """命令行入口。"""
    parser = argparse.ArgumentParser(description="MACD+均线 A 股筛选器（第 10 步）")
    parser.add_argument("--date", default=None, help="分析日期 YYYY-MM-DD（默认今天）")
    parser.add_argument("--push", type=int, default=1, help="是否飞书推送（1=是 0=否）")
    parser.add_argument("--min-cap", type=float, default=50.0, help="最小市值（亿元，默认50）")
    parser.add_argument("--top", type=int, default=0, help="只输出前N只（0=全部）")
    args = parser.parse_args()

    # 从 _core_helpers 统一加载 .env（与其他脚本保持一致；异常直接抛出，
    # 避免 try/except 静默吞掉导致 HITHINK_FINANCE_API_KEY 等变量未加载）
    from trade_krono_cli.cli_commands._core_helpers import _load_env

    _load_env()

    results = run(
        date_str=args.date,
        push=args.push,
        min_cap_yi=args.min_cap,
        top_n=args.top,
    )
    logger.info(f"RESULT_COUNT={len(results)}")


if __name__ == "__main__":
    main()
