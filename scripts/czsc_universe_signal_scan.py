#!/usr/bin/env python3
"""
全市场 CZSC 缠论信号扫描任务

功能：
1. DB → parquet 转换（增量：只转换最新日期有更新的标的）
2. 全市场信号计算（并行，8 进程，约 2 分钟）
3. 覆盖度校验（当日有行情的标的数 / 总标的数）

调度建议：每个交易日 16:20 执行（收盘后，T+0 可用）
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import pickle
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime
from pathlib import Path

import pandas as pd
from loguru import logger

from scripts._utils import get_sqlite_conn

# ── 常量 ─────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "outputs/cache/pipeline_cache.db"
BARS_DIR = PROJECT_ROOT / "outputs/czsc_bars"
OUT_DIR = PROJECT_ROOT / "outputs/czsc_signal_scan"
LOG_DIR = PROJECT_ROOT / "outputs/logs"

# 脚本直接运行时 sys.path[0] 指向 scripts/ 目录，需显式加入项目根以便导入 scripts.* 包
sys.path.insert(0, str(PROJECT_ROOT))

# 只导出 sh/sz/bj 的标的（去掉 hk）
VALID_PREFIXES = ("sh.", "sz.", "bj.")

# 选股信号配置：笔向上 AND 20日均线多头 AND NOT 跌停
SIGNALS_CONFIG: list[dict[str, object]] = [
    {"name": "cxt_bi_status_V230101", "freq": "日线"},
    {"name": "tas_ma_base_V221101", "freq": "日线", "di": 1, "timeperiod": 20, "ma_type": "SMA"},
    {"name": "bar_zdt_V230331", "freq": "日线", "di": 1},
]
CONDITION: dict[str, object] = {
    "name": "日线笔向上_均线多头",
    "operate": "开多",
    "signals_all": [
        "日线_D1_表里关系V230101_向上_任意_任意_0",
        "日线_D1SMA#20_分类V221101_多头_任意_任意_0",
    ],
    "signals_not": ["日线_D1_涨跌停V230331_跌停_任意_任意_0"],
}

# 额外信号（三买 + 笔向上）
SIGNALS_CONFIG_THIRD_BUY: list[dict[str, object]] = [
    {"name": "cxt_third_buy_V230228", "freq": "日线", "di": 1},
    {"name": "cxt_bi_status_V230101", "freq": "日线"},
]
CONDITION_THIRD_BUY: dict[str, object] = {
    "name": "日线三买_笔向上",
    "operate": "开多",
    "signals_all": [
        "日线_D1_三买辅助V230228_三买_任意_任意_0",
        "日线_D1_表里关系V230101_向上_任意_任意_0",
    ],
}

MIN_BARS = 60
INIT_N = 250
WORKERS = int(os.cpu_count() or 8)

# ── 工具函数 ─────────────────────────────────────────────────────

def ensure_dirs() -> None:
    BARS_DIR.mkdir(parents=True, exist_ok=True)
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)


def get_latest_bar_date(ticker: str) -> str | None:
    """读取 parquet 获取最后交易日（YYYY-MM-DD）"""
    path = BARS_DIR / f"{ticker}.parquet"
    if not path.exists():
        return None
    try:
        df = pd.read_parquet(path, columns=["dt"])
        return str(df["dt"].max().date())
    except Exception as e:
        logger.warning(f"⚠️  读取 {path} 失败: {e}")
        return None


def get_db_latest_date(ticker: str) -> str | None:
    """读取 DB 获取最后交易日"""
    try:
        con = get_sqlite_conn(DB_PATH, readonly=True)
        row = con.execute(
            "select end from kline_cache where ticker=? and freq='d'", (ticker,)
        ).fetchone()
        con.close()
        return row[0] if row else None
    except Exception as e:
        logger.warning(f"⚠️  查询 {ticker} 最新日期失败: {e}")
        return None


def db_to_parquet_incremental() -> tuple[int, int]:
    """
    增量转换：只转换 DB 中 end > parquet 最后日期的标的
    返回 (新转换数, 跳过数)
    """
    con = get_sqlite_conn(DB_PATH, readonly=True)
    cur = con.cursor()
    rows = cur.execute(
        "select ticker, data from kline_cache where freq='d' and (ticker like ? or ticker like ? or ticker like ?)",
        (f"{VALID_PREFIXES[0]}%", f"{VALID_PREFIXES[1]}%", f"{VALID_PREFIXES[2]}%"),
    ).fetchall()
    con.close()

    converted = skipped = 0
    for ticker, blob in rows:
        db_end = get_db_latest_date(ticker)
        bar_end = get_latest_bar_date(ticker)
        if bar_end and db_end and db_end <= bar_end:
            skipped += 1
            continue
        df = pickle.loads(blob)
        if len(df) < MIN_BARS:
            skipped += 1
            continue
        ts = df["timestamps"]
        if ts.dtype == object or str(ts.dtype).startswith("str"):
            ts = pd.to_datetime(ts, format="mixed")
        out = pd.DataFrame(
            {
                "dt": pd.to_datetime(ts).astype("datetime64[ns]"),
                "symbol": ticker,
                "open": df["open"].astype("float64"),
                "close": df["close"].astype("float64"),
                "high": df["high"].astype("float64"),
                "low": df["low"].astype("float64"),
                "vol": df["volume"].astype("float64"),
                "amount": df["amount"].astype("float64"),
            }
        )
        out = out.dropna(subset=["open", "close", "high", "low", "vol", "amount"])
        out = out.sort_values("dt", kind="stable").drop_duplicates("dt", keep="last").reset_index(drop=True)
        if out.empty:
            skipped += 1
            continue
        out.to_parquet(BARS_DIR / f"{ticker}.parquet", index=False)
        converted += 1
    return converted, skipped


# ── 信号计算 worker ──────────────────────────────────────────────

def signal_key(full: str) -> str:
    return "_".join(full.split("_")[:3])


def screen_one(job: tuple[str, str | None, list[dict], dict]) -> dict:
    path, asof, cfg, cond = job
    df = pd.read_parquet(path)
    df["dt"] = pd.to_datetime(df["dt"])
    df = df.sort_values("dt", kind="stable").drop_duplicates("dt", keep="last").reset_index(drop=True)
    if len(df) < MIN_BARS:
        return {"symbol": Path(path).stem, "matched": False, "dt": None, "close": None, "signals": {}}

    # 延迟导入，避免多进程重复导入 czsc 的开销
    from czsc import Event, Freq, format_standard_kline, generate_czsc_signals

    bars = format_standard_kline(df, freq=Freq("日线"))
    out = generate_czsc_signals(bars, signals_config=cfg, df=True, init_n=min(INIT_N, max(10, len(bars) - 1)))
    out["dt"] = pd.to_datetime(out["dt"], utc=True).dt.tz_localize(None)
    if asof:
        out = out[out["dt"] <= pd.Timestamp(asof)]
    if out.empty:
        return {"symbol": Path(path).stem, "matched": False, "dt": None, "close": None, "signals": {}}

    row = out.iloc[-1]
    cols = [c for c in out.columns if len(str(c).split("_")) == 3]
    all_signals = {c: row[c] for c in cols}
    event = Event.load(cond)
    matched = bool(event.is_match(all_signals))
    keys = {signal_key(s) for s in event.unique_signals}
    return {
        "symbol": Path(path).stem,
        "matched": matched,
        "dt": str(row["dt"].date()) if row["dt"] is not None else "",
        "close": float(row["close"]) if row["close"] is not None else 0.0,
        "signals": {k: v for k, v in all_signals.items() if k in keys},
        "matched_signals": event.unique_signals if matched else [],
    }


def run_screen(
    asof: str | None,
    cfg: list[dict[str, object]],
    cond: dict[str, object],
    preset_name: str,
) -> dict:
    """跑一遍全市场筛选"""
    paths = sorted(BARS_DIR.glob("*.parquet"))
    if not paths:
        return {"hits": 0, "total": 0, "hit_symbols": [], "seconds": 0.0}

    t0 = time.perf_counter()
    jobs = [(str(p), asof, cfg, cond) for p in paths]
    with ProcessPoolExecutor(max_workers=WORKERS) as ex:
        results = list(ex.map(screen_one, jobs, chunksize=4))
    elapsed = time.perf_counter() - t0

    hits = [r for r in results if r["matched"]]
    out_csv = OUT_DIR / f"matched_{preset_name}_{datetime.now().strftime('%Y%m%d')}.csv"
    pd.DataFrame(
        [
            {
                "symbol": r["symbol"],
                "dt": r["dt"],
                "close": r["close"],
                "signals": json.dumps(r["signals"], ensure_ascii=False),
                "matched_signals": json.dumps(r["matched_signals"], ensure_ascii=False),
            }
            for r in hits
        ],
        columns=["symbol", "dt", "close", "signals", "matched_signals"],
    ).to_csv(out_csv, index=False, encoding="utf-8-sig")

    return {
        "preset": cond["name"],
        "asof": asof,
        "universe": len(results),
        "hits": len(hits),
        "hit_symbols": [r["symbol"] for r in hits],
        "seconds": round(elapsed, 2),
        "workers": WORKERS,
        "output_csv": str(out_csv),
    }


def daily_coverage(asof: str) -> dict:
    """统计当日 active 标的数 & 命中率"""
    from czsc import Event, Freq, format_standard_kline, generate_czsc_signals

    paths = sorted(BARS_DIR.glob("*.parquet"))
    ev = Event.load(CONDITION)
    hits_count = 0
    active_count = 0
    for p in paths:
        df = pd.read_parquet(p, columns=["dt"])
        dmax = df["dt"].max()
        if dmax is None:
            continue
        dmax_str = str(dmax.date())
        if dmax_str == asof:
            active_count += 1
            # 只在 active 时算信号（省成本）
            full = pd.read_parquet(p)
            full["dt"] = pd.to_datetime(full["dt"])
            full = full.sort_values("dt", kind="stable").drop_duplicates("dt", keep="last").reset_index(drop=True)
            if len(full) < MIN_BARS:
                continue
            bars = format_standard_kline(full, freq=Freq("日线"))
            out = generate_czsc_signals(bars, signals_config=SIGNALS_CONFIG, df=True, init_n=INIT_N)
            out["dt"] = pd.to_datetime(out["dt"], utc=True).dt.tz_localize(None)
            row = out[out["dt"] <= pd.Timestamp(asof)].tail(1)
            if row.empty:
                continue
            cols = [c for c in out.columns if len(str(c).split("_")) == 3]
            if ev.is_match({c: row.iloc[0][c] for c in cols}):
                hits_count += 1

    return {
        "date": asof,
        "active_symbols": active_count,
        "hits": hits_count,
        "hit_rate": round(hits_count / active_count, 4) if active_count else None,
    }


def _read_hit_details(csv_path: Path) -> list[dict]:
    """读取命中股票详情（symbol / dt / close / signals / matched_signals）。"""
    if not csv_path.exists():
        return []
    rows: list[dict] = []
    with open(csv_path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(
                {
                    "symbol": row.get("symbol", ""),
                    "dt": row.get("dt", ""),
                    "close": row.get("close", ""),
                    "signals": row.get("signals", ""),
                    "matched_signals": row.get("matched_signals", ""),
                }
            )
    return rows


def _format_matched_signals(raw: object, limit: int = 3) -> str:
    """把 CSV 中的 matched_signals（JSON 字符串或列表）格式化为简洁的信号名。

    过滤掉「跌停」等否定信号（它们只表示排除条件，并不是入选理由）。

    Args:
        raw: 原始 matched_signals 值（JSON 字符串 / list / 空值）。
        limit: 最多展示的信号条数，默认 3。

    Returns:
        以 ", " 连接的信号名，解析失败时返回空字符串。
    """
    items: list[str]
    if isinstance(raw, list):
        items = [str(s) for s in raw]
    else:
        try:
            items = [str(s) for s in json.loads(str(raw))]
        except (json.JSONDecodeError, TypeError):
            items = [s.strip().strip('"[]') for s in str(raw).split(",") if s.strip()]
    positive = [s for s in items if s and "跌停" not in s]
    return ", ".join(positive[:limit])


def _build_czsc_feishu_content(date_str: str, r1: dict, r2: dict) -> str:
    """构建飞书推送文本内容（两个预设分开展示，各含命中股票+日期+收盘价+信号键值）。

    这是「缠论信号选股」独立于综合交集汇总的第 2 个筛选结果。
    """
    lines: list[str] = []
    lines.append(f"📊 **{date_str} 缠论信号全市场选股**")
    lines.append("")

    # 预设 A: bi_ma
    lines.append("🔹 **预设 A：笔向上 + 均线多头 (bi_ma)**")
    lines.append(f"  命中：{r1['hits']}/{r1['universe']} 只，耗时 {r1['seconds']}s")
    details = _read_hit_details(Path(r1.get("output_csv", "")))
    if details:
        lines.append("  命中股票（前 10 只，格式：代码 日期 收盘 信号键值）：")
        for d in details[:10]:
            sig_keys = _format_matched_signals(d.get("matched_signals", ""))
            lines.append(f"    - {d['symbol']} {d['dt']} 收盘 {d['close']} {sig_keys}")
        if len(details) > 10:
            lines.append(f"    ... 共 {len(details)} 只")
    lines.append("")

    # 预设 B: third_buy
    lines.append("🔹 **预设 B：三买 + 笔向上 (third_buy)**")
    lines.append(f"  命中：{r2['hits']}/{r2['universe']} 只，耗时 {r2['seconds']}s")
    details = _read_hit_details(Path(r2.get("output_csv", "")))
    if details:
        lines.append("  命中股票（前 10 只，格式：代码 日期 收盘 信号键值）：")
        for d in details[:10]:
            sig_keys = _format_matched_signals(d.get("matched_signals", ""))
            lines.append(f"    - {d['symbol']} {d['dt']} 收盘 {d['close']} {sig_keys}")
        if len(details) > 10:
            lines.append(f"    ... 共 {len(details)} 只")
    lines.append("")

    total = r1["hits"] + r2["hits"]
    lines.append(f"📈 合计命中：{total} 只（两预设，未去重）")
    lines.append("📁 详细结果：outputs/czsc_signal_scan/")
    lines.append("")
    lines.append("※ 这是「缠论信号选股」独立于「综合交集汇总」的第 2 个筛选结果。")

    return "\n".join(lines)


# ── 主入口 ──────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="全市场 CZSC 信号扫描")
    ap.add_argument("--convert-only", action="store_true", help="只做 DB→parquet 增量转换")
    ap.add_argument("--scan-only", action="store_true", help="只跑信号扫描（不转换）")
    ap.add_argument("--asof", default=None, help="截面日期 YYYY-MM-DD，默认最新")
    ap.add_argument(
        "--push",
        type=int,
        default=1,
        help="扫描完成后推送飞书（1=是，默认 1；0=否）",
    )
    args = ap.parse_args()

    ensure_dirs()
    log_file = LOG_DIR / f"czsc_scan_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
    log_f = open(log_file, "w", encoding="utf-8")
    sys.stdout = sys.stderr = log_f  # 双写日志（也保留控制台输出用 tee）

    print(f"[{datetime.now()}] ==== CZSC 全市场信号扫描开始 ====", flush=True)
    print(f"DB: {DB_PATH}", flush=True)
    print(f"BARS: {BARS_DIR}", flush=True)
    print(f"OUT: {OUT_DIR}", flush=True)

    asof = args.asof or datetime.now().strftime("%Y-%m-%d")
    print(f"as-of: {asof}", flush=True)

    # 1) DB → parquet
    if not args.scan_only:
        print("[1/3] DB → parquet 增量转换...", flush=True)
        converted, skipped = db_to_parquet_incremental()
        print(f"     新转换 {converted} 只，跳过 {skipped} 只", flush=True)

    # 2) 信号扫描（两个预设）
    if not args.convert_only:
        print("[2/3] 全市场信号扫描...", flush=True)
        r1 = run_screen(asof, SIGNALS_CONFIG, CONDITION, "bi_ma")
        print(f"     预设 bi_ma: 命中 {r1['hits']}/{r1['universe']} 只，{r1['seconds']}s", flush=True)

        r2 = run_screen(asof, SIGNALS_CONFIG_THIRD_BUY, CONDITION_THIRD_BUY, "third_buy")
        print(f"     预设 third_buy: 命中 {r2['hits']}/{r2['universe']} 只，{r2['seconds']}s", flush=True)

        # 3) 覆盖度校验
        print("[3/3] 覆盖度校验...", flush=True)
        cov = daily_coverage(asof)
        print(f"     {cov['date']}: active={cov['active_symbols']}, hits={cov['hits']}, rate={cov['hit_rate']:.2%}", flush=True)

        # 汇总 JSON
        summary = {
            "date": asof,
            "timestamp": datetime.now().isoformat(),
            "presets": [r1, r2],
            "coverage": cov,
            "conversion": {"converted": converted, "skipped": skipped} if not args.scan_only else {},
        }
        summary_path = OUT_DIR / f"summary_{asof}.json"
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[done] 汇总: {summary_path}", flush=True)

        # 4) 飞书推送（两个预设结果，独立于综合交集汇总）
        if args.push:
            print("[4/4] 飞书推送...", flush=True)
            from scripts.feishu_utils import push_feishu  # noqa: PLC0415
            content = _build_czsc_feishu_content(asof, r1, r2)
            ok = push_feishu(
                "text",
                content=content,
                title=f"📊 {asof} 缠论信号全市场选股",
            )
            print(f"     飞书推送：{'成功' if ok else '失败'}", flush=True)

    log_f.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
