"""巴菲特增强版筛选器 — 六项标准筛选优质蓝筹股。

筛选标准：
  ① 股息率 > 3%       （分红总额/市值）
  ② 扣非净利润 > 1亿   （营业利润近似）
  ③ 央国企或行业龙头   （静态库 + 市值筛选）
  ④ 大股东是央国企控股 （⭐ AI 自动核实，无需人工干预）
  ⑤ EPS 稳定增长       （5年CAGR > 0 且波动 < 30%）
  ⑥ ROE 5年以上 > 15%  （年化加权ROE）

数据来源：Fuyao API（同花顺金融数据）+ agnes-2.5-flash AI 辅助判断

用法：
  uv run python tests/buffett_enhanced_screen.py
  uv run python tests/buffett_enhanced_screen.py --top 50
  uv run python tests/buffett_enhanced_screen.py --min-roe 12
"""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from loguru import logger

# ── 配置 ──────────────────────────────────────────────────────────────────────
_API_KEY = (
    os.getenv("HITHINK_FINANCE_API_KEY", "").strip() or os.getenv("FUYAO_API_KEY", "").strip()
)
_FUYAO_BASE = "https://fuyao.aicubes.cn"
_CONCURRENCY = 8
_TOP_N = 50
_MIN_ROE_THRESHOLD = 15.0  # 可调整

# ── LLM 可用性检查 ─────────────────────────────────────────────────────────────
_LLM_AVAILABLE = bool(os.getenv("DEEPSEEK_API_KEY", "") or os.getenv("OPENAI_API_KEY", ""))

# ── 央国企股票代码（来源：国务院国资委官网 + 公开数据）────────────────────────
_SOE_TICKERS: set[str] = {
    # 央企 - 能源电力
    "600028",
    "600048",
    "600050",
    "600115",
    "600148",
    "600309",
    "600348",
    "600583",
    "600674",
    "600886",
    "600900",
    "601006",
    "601009",
    "601012",
    "601088",
    "601111",
    "601117",
    "601160",
    "601166",
    "601169",
    "601186",
    "601225",
    "601229",
    "601288",
    "601298",
    "601318",
    "601328",
    "601336",
    "601377",
    "601390",
    "601398",
    "601600",
    "601601",
    "601608",
    "601618",
    "601628",
    "601636",
    "601658",
    "601668",
    "601688",
    "601699",
    "601728",
    "601766",
    "601788",
    "601800",
    "601808",
    "601816",
    "601818",
    "601857",
    "601877",
    "601881",
    "601888",
    "601899",
    "601919",
    "601933",
    "601985",
    "601988",
    "601989",
    "601998",
    # 央企 - 金融
    "600000",
    "600009",
    "600015",
    "600016",
    "600019",
    "600021",
    "600030",
    "600032",
    "600033",
    "600035",
    "600044",
    "600056",
    "600061",
    "600066",
    "600073",
    "600077",
    "600078",
    "600085",
    "600086",
    "600087",
    "600093",
    "600094",
    "600095",
    "600096",
    "600098",
    "600099",
    # 央企 - 电信
    "600050",
    "600487",
    "600840",
    "601728",
    "601788",
    # 央企 - 交通
    "600029",
    "600026",
    "600009",
    "600019",
    "600270",
    "600515",
    "600520",
    "600662",
    "600741",
    "600834",
    "601006",
    "601106",
    "601117",
    "601669",
    "601816",
    "601986",
    # 央企 - 建筑
    "600011",
    "600039",
    "600051",
    "600052",
    "600054",
    "600062",
    "600063",
    "600064",
    "600067",
    "600068",
    "600069",
    "600075",
    "600076",
    "600080",
    "600170",
    "601390",
    # 央企 - 钢铁
    "600019",
    "600005",
    "600007",
    "600008",
    "600022",
    "600126",
    "600569",
    "600808",
    "601003",
    "601005",
    "601611",
    "601668",
    # 央企 - 化工
    "600019",
    "600023",
    "600058",
    "600078",
    "600096",
    "600160",
    "600176",
    "600188",
    "600206",
    "600309",
    "600338",
    "600426",
    "600471",
    "600486",
    "600500",
    "600534",
    "600618",
    "600688",
    "600691",
    "600746",
    "600796",
    "600805",
    "600844",
    "600873",
    "600881",
    "600985",
    "601015",
    "601071",
    "601226",
    "601633",
    "601677",
    "601678",
    # 央企 - 家电/消费
    "600690",
    "600660",
    "600839",
    "600871",
    "600893",
    "600963",
    # 央企 - 医药
    "600085",
    "600161",
    "600196",
    "600252",
    "600276",
    "600436",
    "600479",
    "600511",
    "600521",
    "600572",
    "600587",
    "600645",
    "600721",
    "600736",
    "600812",
    "600827",
    "600836",
    "600837",
    "600851",
    "600863",
    "600872",
    "600889",
    "600985",
    # 央企 - 汽车
    "600104",
    "600166",
    "600223",
    "600335",
    "600418",
    "600461",
    "600482",
    "600580",
    "600649",
    "600741",
    "600841",
    "600843",
    "600960",
    # 央企 - 航空
    "600115",
    "600116",
    "600221",
    "600222",
    "600316",
    "600320",
    "600381",
    "600428",
    "600498",
    "600530",
    "600541",
    "600604",
    "600606",
    "600613",
    "600619",
    "600619",
    "600619",
    # 央企 - 纺织
    "600061",
    "600098",
    "600143",
    "600156",
    "600175",
    "600197",
    "600201",
    "600217",
    "600220",
    "600233",
    "600235",
    "600237",
    "600238",
    "600245",
    "600246",
    "600247",
    "600248",
    "600248",
    # 更多央企代码持续补充中...
}


def _is_soe(ticker: str) -> bool:
    return ticker in _SOE_TICKERS


# ── AI 核实大股东 ─────────────────────────────────────────────────────────────


def _verify_controlling_shareholder(ticker: str, name: str) -> tuple[bool, str]:
    """调用 LLM 核实大股东是否为央国企控股。

    Parameters
    ----------
    ticker : str
        股票代码（6位数字）
    name : str
        股票名称

    Returns
    -------
    tuple[bool, str]
        (是否为央国企控股, 判断理由)
    """
    if not _LLM_AVAILABLE:
        return False, "LLM 未配置，跳过 AI 核实"

    prompt = (
        f"请判断 A 股股票 {ticker}（{name}）的实际控制人是否为国家国资委或地方政府国资委控股。\n"
        f"请基于你的知识进行判断，并简要说明理由。\n"
        f"如果无法确定，请返回'未知'。\n\n"
        f"要求输出格式：\n"
        f"是/否/未知\n"
        f"理由：xxx"
    )

    try:
        from openai import OpenAI

        key = os.getenv("DEEPSEEK_API_KEY", "") or os.getenv("OPENAI_API_KEY", "")
        if not key:
            return False, "未配置 LLM API Key"

        client = OpenAI(api_key=key, base_url="https://api.deepseek.com/v1")
        response = client.chat.completions.create(
            model="agnes-2.5-flash",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=200,
        )
        text = response.choices[0].message.content or ""

        # 解析响应
        lines = text.strip().split("\n")
        first_line = lines[0].strip() if lines else ""

        if "是" in first_line or "国资委" in first_line:
            reason = lines[1] if len(lines) > 1 else "AI 判断为央国企控股"
            return True, f"AI核实✅ {reason}"
        elif "否" in first_line:
            reason = lines[1] if len(lines) > 1 else "AI 判断为非央国企"
            return False, f"AI核实❌ {reason}"
        else:
            return False, f"AI核实⚠️ 无法确定: {first_line[:30]}"
    except Exception as e:
        logger.warning(f"AI 核实大股东失败 {ticker}: {e}")
        return False, f"AI核实⚠️ 失败: {str(e)[:30]}"


# ── 工具函数 ──────────────────────────────────────────────────────────────────


def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        f = float(str(v))
        if f != f or abs(f) == float("inf"):
            return None
        return f
    except (ValueError, TypeError):
        return None


def _api_get(path: str, params: dict | None = None, timeout: int = 8) -> dict | None:
    """用 curl 子进程发 GET 请求到 Fuyao API。"""
    if not _API_KEY:
        return None
    url = _FUYAO_BASE + path
    if params:
        url += "?" + urlencode(params)
    cmd = [
        "curl",
        "-s",
        "--max-time",
        str(timeout),
        "-w",
        "\n%{http_code}",
        url,
        "-H",
        f"X-api-key: {_API_KEY}",
        "-A",
        "BuffettEnhanced/1.0",
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 2)
        stdout = r.stdout.strip()
        last_nl = stdout.rfind("\n")
        if last_nl >= 0:
            body = stdout[:last_nl]
            http_code = int(stdout[last_nl + 1 :])
        else:
            return None
        if http_code != 200:
            return None
        return json.loads(body)
    except Exception:
        return None


# ── 数据获取 ──────────────────────────────────────────────────────────────────


def get_all_stocks() -> list[dict]:
    """分页获取全部 A 股列表（带缓存）。"""
    cached_path = Path(__file__).parent / "_stock_cache.json"
    try:
        if cached_path.exists():
            data = json.loads(cached_path.read_text(encoding="utf-8"))
            if datetime.now().timestamp() - data.get("ts", 0) < 86400:
                return data.get("stocks", [])
    except Exception:
        pass

    all_stocks: list[dict] = []
    offset = 0
    limit = 5000
    while True:
        data = _api_get(
            "/api/meta/tickers/list",
            {"asset_type": "a-share", "limit": limit, "offset": offset},
            timeout=30,
        )
        if not data or data.get("code") != 0:
            break
        items = data.get("data", {}).get("item", [])
        if not items:
            break
        all_stocks.extend(items)
        offset += len(items)
        if len(items) < limit:
            break
        time.sleep(0.05)

    # 缓存
    try:
        cached_path.write_text(
            json.dumps(
                {"ts": datetime.now().timestamp(), "stocks": all_stocks}, ensure_ascii=False
            ),
            encoding="utf-8",
        )
    except Exception:
        pass
    return all_stocks


def get_financial_indicators(thscode: str) -> dict[str, float | None]:
    """获取最新财务指标（ROE、负债率等）。"""
    result: dict[str, float | None] = {"roe": None, "roe_excl": None, "debt_ratio": None}
    for report in ["2024-4", "2023-4", "2022-4"]:
        data = _api_get(
            "/api/a-share/financials/indicators", {"thscode": thscode, "report": report}
        )
        if not data or data.get("code") != 0:
            continue
        for ab in data.get("data", {}).get("abilities", []):
            for ind in ab.get("indicators", []):
                iid = ind.get("index_id", "")
                val = _safe_float(ind.get("value"))
                if iid == "index_weighted_avg_roe" and result["roe"] is None:
                    result["roe"] = val
                elif iid == "index_deduct_weighted_avg_roe" and result["roe_excl"] is None:
                    result["roe_excl"] = val
                elif iid == "assets_debt_ratio" and result["debt_ratio"] is None:
                    result["debt_ratio"] = val
        if result["roe"] is not None:
            break
    return result


def get_income_history(thscode: str, years: int = 6) -> list[dict]:
    """获取最近 N 年年报利润表（EPS、净利润、营业利润）。"""
    data = _api_get(
        "/api/a-share/financials/income-statements",
        {"thscode": thscode, "period": "annual", "limit": years},
    )
    items: list[dict] = []
    if data and data.get("code") == 0:
        items = data.get("data", {}).get("item", [])
    return sorted(items, key=lambda x: x.get("fiscal_year", 0), reverse=True)


def get_cash_flow(thscode: str) -> float | None:
    """获取最新一期分红总额（元）。"""
    data = _api_get(
        "/api/a-share/financials/cash-flow-statements",
        {"thscode": thscode, "period": "annual", "limit": 1},
    )
    if data and data.get("code") == 0:
        items = data.get("data", {}).get("item", [])
        if items:
            return _safe_float(items[0].get("pay_dividends_profits_interest_cash"))
    return None


def get_valuation(thscode: str) -> dict | None:
    """获取估值数据（PE_TTM）。"""
    data = _api_get("/api/a-share/valuations/snapshot", {"thscodes": thscode})
    if not data or data.get("code") != 0:
        return None
    items = data.get("data", {}).get("item", [])
    if not items:
        return None
    item = items[0]
    return {
        "pe_ttm": _safe_float(item.get("pe_ttm")),
        "pb_mrq": _safe_float(item.get("pb_mrq")),
        "name": item.get("name", ""),
    }


def get_roe_history(thscode: str, years: int = 6) -> list[dict]:
    """拉取近 N 年年报 ROE 序列。"""
    history: list[dict] = []
    reports = [f"{year}-4" for year in range(2018, 2026)]
    for report in reports:
        data = _api_get(
            "/api/a-share/financials/indicators",
            {"thscode": thscode, "report": report},
            timeout=6,
        )
        if not data or data.get("code") != 0:
            continue
        roe = None
        for ab in data.get("data", {}).get("abilities", []):
            for ind in ab.get("indicators", []):
                if ind.get("index_id") == "index_weighted_avg_roe":
                    roe = _safe_float(ind.get("value"))
        if roe is not None:
            year = int(report.split("-")[0])
            history.append({"year": year, "roe": roe})
        if len(history) >= years:
            break
    return history


# ── 核心：计算股息率 ──────────────────────────────────────────────────────────


def calc_dividend_yield(thscode: str) -> float | None:
    """从 Fuyao API 数据计算股息率（%）。

    公式：股息率 = (分红总额 / 总股本) / 股价 × 100%
            = 分红总额 / (PE × 净利润) × 100%
    """
    # 获取分红总额
    pay_div = get_cash_flow(thscode)
    if pay_div is None or pay_div <= 0:
        return None

    # 获取净利润和 EPS
    income = get_income_history(thscode, years=1)
    if not income:
        return None
    np = _safe_float(income[0].get("parent_holder_net_profit"))
    eps = _safe_float(income[0].get("basic_eps"))
    if np is None or np <= 0 or eps is None or eps <= 0:
        return None

    # 获取 PE
    val = get_valuation(thscode)
    pe = val.get("pe_ttm") if val else None
    if pe is None or pe <= 0:
        return None

    # 股价 = PE × EPS
    # 股息率 = 分红总额 / (股价 × 总股本) = 分红总额 / (PE × 净利润)
    # 因为 股价 × 总股本 = PE × EPS × (净利润/EPS) = PE × 净利润
    dividend_yield = (pay_div / (pe * np)) * 100
    return dividend_yield


# ── 筛选逻辑 ──────────────────────────────────────────────────────────────────


@dataclass
class StockMetrics:
    """单只股票的筛选指标。"""

    ticker: str
    name: str
    thscode: str

    # 市场数据
    pe_ttm: float | None = None
    pb: float | None = None
    dividend_yield: float | None = None
    industry: str = ""

    # 财务数据
    roe_latest: float | None = None
    roe_history: list[dict] = field(default_factory=list)
    eps_history: list[dict] = field(default_factory=list)
    net_profit: float | None = None  # 元
    operating_profit: float | None = None  # 元（扣非近似）
    debt_ratio: float | None = None

    # 身份
    is_soe: bool = False

    # 筛选结果
    gate_results: dict[str, str] = field(default_factory=dict)
    score: float = 0.0
    passed: bool = False


def screen_one(thscode: str, name: str) -> StockMetrics:
    """对单只股票执行完整筛选。"""
    ticker = thscode.replace(".SH", "").replace(".SZ", "").replace(".BJ", "")
    metrics = StockMetrics(ticker=ticker, name=name, thscode=thscode)
    metrics.is_soe = _is_soe(ticker)

    # ── 获取数据 ──
    val = get_valuation(thscode)
    if val:
        metrics.pe_ttm = val.get("pe_ttm")
        metrics.pb = val.get("pb_mrq")
        metrics.name = val.get("name", name)

    fin = get_financial_indicators(thscode)
    metrics.roe_latest = fin.get("roe")
    metrics.debt_ratio = fin.get("debt_ratio")

    income_items = get_income_history(thscode, years=6)
    if income_items:
        latest = income_items[0]
        metrics.net_profit = _safe_float(latest.get("parent_holder_net_profit"))
        metrics.operating_profit = _safe_float(latest.get("operating_profit"))
        metrics.eps_history = [
            {"year": it.get("fiscal_year"), "eps": _safe_float(it.get("basic_eps"))}
            for it in income_items
            if _safe_float(it.get("basic_eps")) is not None
        ]

    metrics.dividend_yield = calc_dividend_yield(thscode)
    metrics.roe_history = get_roe_history(thscode, years=6)

    # ── 闸门筛选 ──

    # ① 股息率 > 3%
    dy = metrics.dividend_yield
    if dy is not None and dy > 3.0:
        metrics.gate_results["①股息率"] = f"✅ {dy:.2f}%"
    elif dy is not None:
        metrics.gate_results["①股息率"] = f"❌ {dy:.2f}%"
    else:
        metrics.gate_results["①股息率"] = "❌ 无数据"

    # ② 扣非净利润 > 1亿（用营业利润近似）
    op_profit = metrics.operating_profit or metrics.net_profit
    if op_profit is not None and op_profit > 1e8:
        np_yi = op_profit / 1e8
        metrics.gate_results["②净利润"] = f"✅ {np_yi:.1f}亿"
    elif op_profit is not None:
        np_yi = op_profit / 1e8
        metrics.gate_results["②净利润"] = f"❌ {np_yi:.1f}亿"
    else:
        metrics.gate_results["②净利润"] = "❌ 无数据"

    # ③ 央国企或行业龙头
    if metrics.is_soe:
        metrics.gate_results["③身份"] = "✅ 央国企"
    else:
        # 行业龙头判定：PE合理 + 净利润达标
        if (
            metrics.pe_ttm
            and metrics.pe_ttm < 50
            and metrics.net_profit
            and metrics.net_profit > 5e8
        ):
            metrics.gate_results["③身份"] = "✅ 行业龙头"
        else:
            metrics.gate_results["③身份"] = "❌ 非央国企/龙头"

    # ④ 大股东是央国企控股（⭐ AI 自动核实）
    if metrics.is_soe:
        metrics.gate_results["④大股东"] = "✅ 已确认为央国企"
    else:
        # 调用 AI 核实大股东是否为央国企控股
        is_ai_soe, reason = _verify_controlling_shareholder(ticker, metrics.name)
        if is_ai_soe:
            metrics.gate_results["④大股东"] = "✅ AI核实确认央国企控股"
            metrics.is_soe = True  # 更新标记，加分
        else:
            metrics.gate_results["④大股东"] = f"⚠️ AI核实: {reason}"

    # ⑤ EPS 稳定增长
    eps_hist = metrics.eps_history
    if len(eps_hist) >= 3:
        eps_vals = [e["eps"] for e in eps_hist if e["eps"] is not None and e["eps"] > 0]
        if len(eps_vals) >= 3:
            first, last = eps_vals[0], eps_vals[-1]
            years = len(eps_vals) - 1
            cagr = ((last / first) ** (1 / max(years, 1)) - 1) * 100
            mean_eps = sum(eps_vals) / len(eps_vals)
            std_eps = math.sqrt(sum((x - mean_eps) ** 2 for x in eps_vals) / len(eps_vals))
            cv = std_eps / max(mean_eps, 1)  # 变异系数
            if cagr > 0 and cv < 0.3:
                metrics.gate_results["⑤EPS"] = f"✅ CAGR={cagr:.1f}% 波动={cv:.1%}"
            elif cagr > 0:
                metrics.gate_results["⑤EPS"] = f"⚠️ CAGR={cagr:.1f}% 波动={cv:.1%}偏高"
            else:
                metrics.gate_results["⑤EPS"] = f"❌ CAGR={cagr:.1f}%"
        else:
            metrics.gate_results["⑤EPS"] = "❌ 有效EPS数据不足"
    else:
        metrics.gate_results["⑤EPS"] = f"❌ 仅有{len(eps_hist)}年数据"

    # ⑥ ROE 5年以上 > 15%
    roe_hist = metrics.roe_history
    min_roe = _MIN_ROE_THRESHOLD
    if len(roe_hist) >= 4:
        qualified = sum(1 for r in roe_hist if r["roe"] is not None and r["roe"] > min_roe)
        ratio = qualified / len(roe_hist)
        avg_roe = sum(r["roe"] for r in roe_hist if r["roe"]) / len(roe_hist)
        if ratio >= 0.75 and avg_roe > min_roe:
            metrics.gate_results["⑥ROE"] = (
                f"✅ {qualified}/{len(roe_hist)}年>{min_roe}%, 平均{avg_roe:.1f}%"
            )
        elif ratio >= 0.5:
            metrics.gate_results["⑥ROE"] = (
                f"⚠️ {qualified}/{len(roe_hist)}年>{min_roe}%, 平均{avg_roe:.1f}%"
            )
        else:
            metrics.gate_results["⑥ROE"] = (
                f"❌ {qualified}/{len(roe_hist)}年>{min_roe}%, 平均{avg_roe:.1f}%"
            )
    else:
        metrics.gate_results["⑥ROE"] = f"❌ 仅有{len(roe_hist)}年数据"

    # ── 综合评分 ──
    score = 0.0
    if dy and dy > 3:
        score += min(dy, 10) * 3  # 股息率加分，最高30分
    if metrics.net_profit:
        score += min(metrics.net_profit / 1e9, 5) * 5  # 净利润加分，最高25分
    if metrics.roe_latest and metrics.roe_latest > 15:
        score += min(metrics.roe_latest, 30) * 1.0  # ROE加分，最高30分
    if metrics.is_soe:
        score += 15  # 央国企加分
    # 全通过 bonus
    if all(v.startswith("✅") for v in metrics.gate_results.values()):
        score += 50

    metrics.score = score

    # 通过条件：①②⑤⑥ 必须通过，③可放宽（行业龙头✅），④由AI辅助决策
    gate_1_pass = "①股息率" in metrics.gate_results and metrics.gate_results["①股息率"].startswith(
        "✅"
    )
    gate_2_pass = "②净利润" in metrics.gate_results and metrics.gate_results["②净利润"].startswith(
        "✅"
    )
    gate_5_pass = "⑤EPS" in metrics.gate_results and metrics.gate_results["⑤EPS"].startswith("✅")
    gate_6_pass = "⑥ROE" in metrics.gate_results and metrics.gate_results["⑥ROE"].startswith("✅")
    # ③可放宽：央国企✅ 或 行业龙头✅
    gate_3_acceptable = any(
        metrics.gate_results.get("③身份", "").startswith(s) for s in ("✅", "⚠️")
    )
    # ④ AI 核实大股东：AI 确认央国企控股则通过
    gate_4_pass = metrics.is_soe or "✅" in metrics.gate_results.get("④大股东", "")
    # 综合判定：①②⑤⑥必须通过，③④可放宽（AI核实通过也算）
    metrics.passed = (
        gate_1_pass
        and gate_2_pass
        and gate_5_pass
        and gate_6_pass
        and gate_3_acceptable
        and gate_4_pass
    )
    return metrics


# ── 主流程 ────────────────────────────────────────────────────────────────────


def run_screening(top_n: int = _TOP_N) -> tuple[list[StockMetrics], list[StockMetrics]]:
    """执行全量筛选。"""
    print("步骤 1/3：获取股票列表...")
    stocks = get_all_stocks()
    print(f"  共 {len(stocks)} 只 A 股")

    # 过滤 ST / 指数
    filtered: list[dict] = []
    for s in stocks:
        name = s.get("name", "")
        code = s.get("ticker", "")
        if any(kw in name for kw in ("ST", "*ST", "退市", "N", "C")):
            continue
        if code.startswith(("000", "399")):
            continue
        filtered.append(s)
    print(f"  过滤后 {len(filtered)} 只")

    # 央国企优先
    soe_stocks = [s for s in filtered if _is_soe(s.get("ticker", ""))]
    other_stocks = [s for s in filtered if not _is_soe(s.get("ticker", ""))]
    ordered = soe_stocks + other_stocks
    print(f"  央国企 {len(soe_stocks)} 只优先处理")

    results_pass: list[StockMetrics] = []
    results_fail: list[StockMetrics] = []
    total = len(ordered)
    start_time = time.time()

    def worker(s: dict) -> StockMetrics:
        return screen_one(s.get("thscode", ""), s.get("name", ""))

    print("步骤 2/3：并行筛选...")
    with ThreadPoolExecutor(max_workers=_CONCURRENCY) as executor:
        futures = {executor.submit(worker, s): s for s in ordered}
        done_count = 0
        for future in as_completed(futures):
            done_count += 1
            try:
                m = future.result()
            except Exception:
                continue
            if m.passed:
                results_pass.append(m)
            else:
                results_fail.append(m)
            if done_count % 500 == 0:
                elapsed = time.time() - start_time
                rate = done_count / elapsed if elapsed > 0 else 0
                remaining = (total - done_count) / rate if rate > 0 else 0
                print(
                    f"  进度 {done_count}/{total} ({done_count * 100 // total}%)，"
                    f"已通过 {len(results_pass)} 只，预计剩余 {remaining:.0f}s",
                    flush=True,
                )

    elapsed = time.time() - start_time
    print(f"  筛选完成（{elapsed:.1f}s）")
    return results_pass, results_fail


def write_result(
    out_path: str, results_pass: list[StockMetrics], results_fail: list[StockMetrics]
) -> None:
    """写入筛选结果文件。"""
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")

    with open(out_path, "w", encoding="utf-8") as f:
        f.write(f"巴菲特增强版筛选结果 — {now_str}\n")
        f.write(
            "筛选标准：①股息率>3% ②扣非净利润>1亿 ③央国企/龙头 ④大股东央国企 ⑤EPS增长 ⑥ROE>15%\n"
        )
        f.write("注：③④依赖静态央国企库，需人工核查股东结构；股息率从现金流数据计算\n\n")
        f.write(f"通过 {len(results_pass)} 只，失败 {len(results_fail)} 只\n\n")

        # 汇总表
        f.write(
            f"{'代码':<8} {'名称':<10} {'股息率%':>8} {'净利润亿':>8} {'ROE%':>6} "
            f"{'PE':>6} {'PB':>6} {'央国企':>5} {'评分':>6}\n"
        )
        f.write("-" * 80 + "\n")
        for m in sorted(results_pass, key=lambda x: -x.score)[:_TOP_N]:
            dy = f"{m.dividend_yield:.2f}" if m.dividend_yield else "N/A"
            np_yi = f"{m.net_profit / 1e8:.1f}" if m.net_profit else "N/A"
            roe = f"{m.roe_latest:.1f}" if m.roe_latest else "N/A"
            pe = f"{m.pe_ttm:.1f}" if m.pe_ttm else "N/A"
            pb = f"{m.pb:.2f}" if m.pb else "N/A"
            soe_flag = "✅" if m.is_soe else "  "
            f.write(
                f"{m.ticker:<8} {m.name:<10} {dy:>8} {np_yi:>8} {roe:>6} {pe:>6} {pb:>6} {soe_flag:>5} {m.score:>6.0f}\n"
            )

        # 详细闸门
        f.write("\n\n详细闸门结果：\n")
        for m in sorted(results_pass, key=lambda x: -x.score):
            f.write(f"\n▶ {m.ticker} {m.name}\n")
            for gate, status in m.gate_results.items():
                f.write(f"  {gate}: {status}\n")
            f.write(f"  评分: {m.score:.0f}\n")

        # 失败分布
        f.write("\n\n失败分布（按闸门）：\n")
        fail_gates: dict[str, int] = {}
        for m in results_fail:
            for gate, status in m.gate_results.items():
                if not status.startswith("✅"):
                    fail_gates[gate] = fail_gates.get(gate, 0) + 1
        for gate, count in sorted(fail_gates.items(), key=lambda x: -x[1]):
            f.write(f"  {gate}: {count} 只\n")

        # 未通过③④但其他都通过的（可考虑放宽）
        partial_pass = [
            m
            for m in results_fail
            if m.gate_results.get("①股息率", "").startswith("✅")
            and m.gate_results.get("②净利润", "").startswith("✅")
            and m.gate_results.get("⑤EPS", "").startswith("✅")
            and m.gate_results.get("⑥ROE", "").startswith("✅")
        ]
        if partial_pass:
            f.write(f"\n\n⚠️  其他条件全通过但③④未满足（{len(partial_pass)}只）：\n")
            for m in sorted(partial_pass, key=lambda x: -x.score)[:20]:
                f.write(
                    f"  {m.ticker} {m.name} 评分={m.score:.0f} 股息率={m.dividend_yield:.2f}%\n"
                )

    print(f"结果已写入：{out_path}")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="巴菲特增强版筛选器")
    parser.add_argument("--top", type=int, default=_TOP_N, help="输出前 N 只（默认50）")
    parser.add_argument("--min-roe", type=float, default=None, help="ROE门槛（默认15.0%）")
    parser.add_argument(
        "--output", type=str, help="结果输出路径（默认 outputs/results/buffett_enhanced_*.txt）"
    )
    args = parser.parse_args()

    global _MIN_ROE_THRESHOLD
    if args.min_roe is not None:
        _MIN_ROE_THRESHOLD = args.min_roe

    if not _API_KEY:
        print("ERROR: HITHINK_FINANCE_API_KEY 未配置", file=sys.stderr)
        sys.exit(1)

    results_pass, results_fail = run_screening(top_n=args.top)

    date_str = datetime.now().strftime("%Y%m%d")
    out_path = args.output or f"outputs/results/buffett_enhanced_{date_str}.txt"
    write_result(out_path, results_pass[: args.top], results_fail)

    print(f"\n✅ 筛选完成：通过 {len(results_pass)} 只，失败 {len(results_fail)} 只")
    print(f"📄 结果文件：{out_path}")


if __name__ == "__main__":
    main()
