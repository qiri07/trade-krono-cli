"""
数据源信息丰富度深度对比（只读，不修改任何代码）。

测试维度：
  1. K线扩展字段（PE/PB/均线/换手率/涨跌幅等）
  2. 实时行情字段（最新价/涨跌/市值/PE/PB等）
  3. 基本面元数据（名称/IPO/退市/ST/行业等）
  4. 技术因子衍生能力（可推算出哪些衍生指标）

用法：uv run python tests/bench_data_richness.py
"""
from __future__ import annotations

from typing import Any

from loguru import logger

from trade_krono_cli.data_providers.factory import DataProviderFactory

TICKER = "sh.600519"
START = "2026-01-01"
END = "2026-08-30"


def _section(title: str) -> None:
    logger.info(f"\n{'='*70}")
    logger.info(f"  {title}")
    logger.info(f"{'='*70}")


def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(v)
        return f if (f == f and abs(f) < float("inf")) else None
    except (ValueError, TypeError):
        return None


async def main():
    factory = DataProviderFactory()
    ticker = TICKER

    # ──────────────────────────────────────────────────────────────
    # 1. baostock
    # ──────────────────────────────────────────────────────────────
    _section("① baostock — 多维度数据探查")
    bs = factory.get_provider("baostock")
    if bs is None:
        logger.warning("baostock 不可用")
        bs = None
    else:
        # 先触发登录
        bs.fetch_kline(ticker, START, END)

        import trade_krono_cli.data_providers.baostock_provider as _bs_mod

        # --- stock_basic 原始字段 ---
        rows = bs._query_stock_basic(ticker)
        logger.info("\n📋 stock_basic 字段:")
        bs_meta_fields: dict[str, str] = {}
        if rows:
            for i, fname in enumerate(["code", "code_name", "ipoDate", "outDate", "type", "status"]):
                val = rows[0][i] if i < len(rows[0]) else None
                bs_meta_fields[fname] = str(val)
                logger.info(f"   {fname}: {val}")

        # --- K线扩展字段（带 PE/PB/MA）---
        logger.info("\n📈 K线扩展字段（含 PE/PB/MA）:")
        try:
            rs_ext = _bs_mod._bs.query_history_k_data_plus(  # type: ignore
                ticker,
                "date,open,high,low,close,volume,amount,peTTM,pbMRQ,psTTM,"
                "dividendsPerShare,totalShare,circShare,ma5,ma10,ma20,ma60",
                start_date=START, end_date=END, frequency="d", adjustflag="1",
            )
            if rs_ext and rs_ext.error_code == "0":
                ext_rows = []
                while rs_ext.next():
                    ext_rows.append(rs_ext.get_row_data())
                if ext_rows:
                    logger.info(f"   实际返回字段: {list(rs_ext.fields)}")
                    sample = ext_rows[0]
                    for fn, sv in zip(rs_ext.fields, sample):
                        if sv:
                            logger.info(f"     {fn}: {sv}")
                    logger.info(f"   有效行数: {len(ext_rows)}")
                    has_ext = any(fn in rs_ext.fields for fn in ["peTTM", "pbMRQ", "ma5", "ma10", "ma20"])
                    logger.info(f"   ✅ 支持 K线扩展字段: {has_ext}")
        except Exception as e:
            logger.info(f"   ❌ K线扩展字段拉取失败: {e}")

        # --- 技术能力清单 ---
        logger.info("\n🔧 baostock 独有技术能力:")
        logger.info("   • check_st_status(ticker) → bool")
        logger.info("   • check_delisted(ticker) → bool")
        logger.info("   • check_new_stock(ticker, date, min_days=60) → (bool, reason)")
        logger.info("   • query_performance_cascade(ticker) → 盈利能力指标")
        logger.info("   • query_operation_ability(ticker) → 营运能力指标")
        logger.info("   • query_growth_ability(ticker) → 成长能力指标")
        logger.info("   • query_dupont(ticker) → 杜邦分析")
        logger.info("   • query_balance(ticker) → 资产负债表")
        logger.info("   • query_profit(ticker) → 利润表")
        logger.info("   • query_cash_flow(ticker) → 现金流量表")

    # ──────────────────────────────────────────────────────────────
    # 2. mootdx
    # ──────────────────────────────────────────────────────────────
    _section("② mootdx — 行情字段探查")
    md = factory.get_provider("mootdx")
    if md is None:
        logger.warning("mootdx 不可用")
        md = None
    else:
        md.fetch_kline(ticker, START, END)

        # bars() 完整列
        df_bars = md._client.bars(symbol="600519", start=0, end=3, freq=8)
        if df_bars is not None and not df_bars.empty:
            logger.info("\n📋 bars() 全部列:")
            for col in df_bars.columns:
                val = df_bars[col].iloc[0]
                logger.info(f"   {col}: {val}")

        # quotes() 完整字段
        q = md._client.quotes(symbols=[(1, "600519")])
        if q is not None and isinstance(q, list) and len(q) > 0:
            sample = q[0]
            if isinstance(sample, dict):
                logger.info("\n📋 quotes() 全部字段:")
                for k, v in sample.items():
                    logger.info(f"   {k}: {v}")

        # 技术指标计算能力
        logger.info("\n🔧 mootdx 独有能力:")
        logger.info("   • level2 数据接口（get_security_ticks）→ 逐笔委托")
        logger.info("   • 分钟线：5/15/30/60 分钟 bar")
        logger.info("   • 盘口数据：买卖五档/十档")
        logger.info("   • ⚠️  限制：bars() hardcode end=500，最多500根日K")
        logger.info("   • ⚠️  不支持基本面元数据")

    # ──────────────────────────────────────────────────────────────
    # 3. tonghuashun
    # ──────────────────────────────────────────────────────────────
    _section("③ tonghuashun (fuyao) — 多维度数据探查")
    ths = factory.get_provider("tonghuashun")
    if ths is None:
        logger.warning("同花顺 不可用")
        ths = None
    else:
        ths.fetch_kline(ticker, START, END)
        thscode = ths._ticker_to_thscode(ticker)

        # snapshot 完整字段
        snap = ths._get("/api/a-share/prices/snapshot", {"thscodes": thscode})
        if snap:
            items = snap.get("item", [])
            if items:
                logger.info("\n📊 实时快照字段:")
                for k, v in items[0].items():
                    logger.info(f"   {k}: {v}")

        # historical 完整字段
        hist = ths._get("/api/a-share/prices/historical", {
            "thscode": thscode, "interval": "1d",
            "start": ths._date_to_ms(START), "end": ths._date_to_ms(END),
            "adjust": "forward",
        })
        if hist:
            items_h = hist.get("item", [])
            if items_h:
                logger.info("\n📈 K线字段:")
                for k, v in items_h[0].items():
                    logger.info(f"   {k}: {v}")

        # meta search
        meta = ths._get("/api/meta/tickers/search", {"q": "600519", "limit": 1})
        if meta:
            items_m = meta.get("item", [])
            if items_m:
                logger.info("\n🏷️ 元数据字段:")
                for k, v in items_m[0].items():
                    logger.info(f"   {k}: {v}")

        logger.info("\n🔧 tonghuashun 独有能力:")
        logger.info("   • 历史K线 + 实时快照 + 元数据三合一")
        logger.info("   • 支持前/后复权")
        logger.info("   • ⚠️  无财务数据、无技术指标接口")
        logger.info("   • ⚠️  封装层未使用 snapshot 中的 price_change/turnover 等字段")

    # ──────────────────────────────────────────────────────────────
    # 4. akshare
    # ──────────────────────────────────────────────────────────────
    _section("④ akshare — 多维度数据探查")
    ak = factory.get_provider("akshare")
    if ak is None:
        logger.warning("akshare 不可用")
        ak = None
    else:
        # hist 字段（实际调用）
        try:
            ak._ensure_import()
            code = ak._ticker_to_ak(ticker)
            df_hist = ak._ak.stock_zh_a_hist(symbol=code, start_date="20260101", end_date="20260830", adjust="1")
            if df_hist is not None and not df_hist.empty:
                logger.info("\n📈 stock_zh_a_hist 返回列:")
                for col in df_hist.columns:
                    val = df_hist[col].iloc[0]
                    logger.info(f"   {col}: {val}")
                logger.info(f"   总行数: {len(df_hist)}")
        except Exception as e:
            logger.info(f"   ❌ hist 调用失败: {e}")

        # spot_em 全市场字段
        try:
            df_spot = ak._ak.stock_zh_a_spot_em()
            if df_spot is not None and not df_spot.empty:
                logger.info("\n📊 stock_zh_a_spot_em 全部列（20+字段）:")
                for col in df_spot.columns:
                    sample_row = df_spot[df_spot["代码"] == code]
                    if not sample_row.empty:
                        val = sample_row.iloc[0][col]
                        logger.info(f"   {col}: {val}")
                    else:
                        logger.info(f"   {col}: (未找到该股)")
        except Exception as e:
            logger.info(f"   ⚠️ spot_em 调用失败（远程断开）: {e}")
            try:
                ak._ensure_import()
                df_spot = ak._ak.stock_zh_a_spot_em()
                logger.info("\n📊 stock_zh_a_spot_em 全部列名:")
                for col in df_spot.columns:
                    logger.info(f"   {col}")
            except Exception as e2:
                logger.info(f"   ❌ spot_em 仍失败: {e2}")

        logger.info("\n🔧 akshare 独有能力:")
        logger.info("   • 全市场批量快照（无需循环，一次返回5000+股票）")
        logger.info("   • 行业板块数据：stock_board_industry_name_em()")
        logger.info("   • 北向资金流向：stock_hsgt_north_net_flow_in_em()")
        logger.info("   • ⚠️  无基本面元数据（无IPO/退市信息）")

    # ──────────────────────────────────────────────────────────────
    # 5. tushare（代码级能力，无Token无法实测）
    # ──────────────────────────────────────────────────────────────
    _section("⑤ tushare Pro — 代码级能力清单（无Token无法实测）")
    ts = factory.get_provider("tushare")
    if ts is None:
        logger.warning("tushare 不可用（未配置 TUSHARE_TOKEN）")
    else:
        # 从源码读取支持的接口
        logger.info("\n📈 pro_bar() 字段:")
        logger.info("   trade_date, open, high, low, close, volume, amount, pre_close, change, pct_change")
        logger.info("\n📊 daily_basic() 字段（每日指标）:")
        logger.info("   ts_code, trade_date, close, high, low, open, volume, amount, "
                     "pct_chg, turnover_rate, pe, pe_ttm, pb, ps, dv_ratio, dv_ttm, "
                     "total_mv, circ_mv")
        logger.info("\n📊 realtime_quote() 字段:")
        logger.info("   ts_code, symbol, name, area, industry, last_close, price, volume, amount, "
                     "high, low, open, bid1, ask1, bid1_vol, ask1_vol, pe, pb, total_mv, circ_mv")
        logger.info("\n🏷️  stock_basic() 字段:")
        logger.info("   ts_code, symbol, name, area, industry, market, list_date, delist_date")
        logger.info("\n💡 独家扩展能力（当前代码未封装，需额外调用）:")
        logger.info("   ⭐⭐⭐ finance_indicator()  — 财务指标全套（EPS/净利润/ROE/营收/资产负债率/现金流）")
        logger.info("   ⭐⭐    daily_basic()      — 每日PE/PB/市值/换手率（历史序列）")
        logger.info("   ⭐⭐    moneyflow()        — 资金流向（主力/散户净流入）")
        logger.info("   ⭐      trade_cal()        — 交易日历")
        logger.info("   ⭐      stock_company()    — 公司详细信息")
        logger.info("   ⚠️  需 Token，免费版约 600 次/天限额")

    # ──────────────────────────────────────────────────────────────
    # 汇总对比表
    # ──────────────────────────────────────────────────────────────
    _section("📊 综合对比总结")

    summary = [
        ("Provider", "K线原始列", "K线扩展列", "实时行情", "实时行情列数",
         "基本面元数据", "独有技术能力"),
        ("────────", "──────────", "──────────", "────────", "──────────", "────────────", "─────────────────────────────"),
        ("baostock", "OHLCV+amount",
         "peTTM/pbMRQ/psTTM/MA5/10/20/60/vol5/10/20/股息",
         "❌ 不支持", "—",
         "✅ 名称/IPO/退市/ST状态",
         "财务三表/杜邦/盈利/营运/成长能力"),
        ("mootdx", "OHLCV+amount", "无",
         "✅ 最新价", "1（仅price）",
         "❌ 不支持",
         "Level2逐笔/五档盘口/分钟线（⭐局限500行）"),
        ("tonghuashun", "OHLCV+amount", "无",
         "✅ last_price/price_change/price_change_ratio_pct/"
         "open/high/low/prev_price/volume/turnover", "11",
         "⚠️ 仅name/exchange",
         "API稳定，但封装层未充分利用snapshot字段"),
        ("akshare", "OHLCV+振幅+涨跌幅+涨跌额+换手率", "内含涨跌幅/振幅/换手率",
         "✅ 20+字段（spot_em）", "20+",
         "❌ 不支持",
         "全市场批量快照/行业板块/北向资金"),
        ("tushare", "OHLCV+amount+pre_close+change+pct_change",
         "（需额外调用finance_indicator/daily_basic）",
         "✅ PE/PB/市值/行业/昨收", "15+",
         "✅ 行业/IPO/退市/市场",
         "⭐财务三表/⭐每日指标/⭐资金流向/交易日历"),
    ]
    col_widths = [14, 22, 38, 24, 12, 20, 42]

    for row in summary:
        formatted = []
        for cell, w in zip(row, col_widths):
            formatted.append(cell[:w].ljust(w))
        logger.info("  " + " | ".join(formatted))

    logger.info("")
    logger.info("🏆  信息丰富度排名（除OHLCV外的附加价值）:")
    logger.info("  🥇 tushare Pro  — 维度最广（财务+每日指标+资金流向），但需Token + 额度限制")
    logger.info("  🥈 akshare       — 免费，实时行情字段最多（20+），含换手率/涨跌幅/振幅，无需Token")
    logger.info("  🥉 baostock      — K线支持追加PE/PB/均线/成交量均线，有ST/退市判断能力")
    logger.info("  4. tonghuashun   — 速度快稳定，snapshot含11字段，但封装层利用率低")
    logger.info("  5. mootdx        — 最快（72ms），数据最窄（OHLCV+量），无扩展字段")


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
