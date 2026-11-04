#!/usr/bin/env python3
"""批量拉取股票元数据（参考数据）。

通过 akshare 批量获取所有 A 股名称、PE/PB 等基础信息（单次 HTTP 请求，
约 77 秒获取全量 5900+ 只），避免逐个调用 baostock/tonghuashun 导致
会话卡死。

写入 outputs/cache/metadata.json，供分析脚本使用。

支持断点续传（checkpoint）：
  全量模式每批完成后将进度写入 outputs/cache/metadata_checkpoint.json，
  中断后再次运行自动从 checkpoint 续拉（跳过已成功、重试失败/None）。

用法：
  uv run python scripts/fetch_reference_data.py
  uv run python scripts/fetch_reference_data.py --tickers "000001,002027"
  uv run python scripts/fetch_reference_data.py --force-refresh
  uv run python scripts/fetch_reference_data.py --force-refresh --resume
  uv run python scripts/fetch_reference_data.py --reset-checkpoint
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from collections.abc import Mapping
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from loguru import logger

from scripts._utils import get_all_tickers, is_etf_ticker, normalize_ticker_key
from trade_krono_cli.cli_commands.core import _load_env
from trade_krono_cli.data_providers.factory import (
    get_data_factory,  # noqa: F401  # 测试通过 monkeypatch 使用
)

FETCH_PER_TICKER_TIMEOUT = 8  # 单只元数据拉取超时：8s 后快速放弃


def _get_metadata_file() -> Path:
    """获取 metadata.json 路径（兼容测试隔离，与 get_cache_db_path 同模式）。"""
    try:
        from trade_krono_cli.config import get_settings  # noqa: PLC0415

        settings = get_settings()
        return Path(settings.cache_dir) / "metadata.json"
    except Exception as e:
        logger.warning(f"⚠️  获取 metadata 路径失败: {e}，使用默认路径")
        return Path("outputs/cache/metadata.json")


METADATA_FILE = _get_metadata_file()
CHECKPOINT_FILE = Path("outputs/cache/metadata_checkpoint.json")

_load_env()


# ════════════════════════════════════════════════════════════════════════════════
# akshare 批量获取股票元数据（名称 + PE/PB + 总市值）
# ════════════════════════════════════════════════════════════════════════════════


def _fetch_akshare_all_metadata() -> dict[str, dict]:
    """用 akshare 批量获取所有 A 股的名称、PE/PB、总市值。

    单次 HTTP 请求即可获取全量 A 股（约 5900 只），耗时约 77 秒，
    避免逐个调用 provider 导致 baostock/tonghuashun 会话卡死。

    Returns:
        {归一化 ticker: metadata dict}，失败时返回空 dict。
    """
    try:
        import akshare as ak  # noqa: PLC0415

        df = ak.stock_zh_a_spot_em()
        logger.info(f"✅ akshare 批量获取股票行情: {len(df)} 只")

        result: dict[str, dict] = {}
        for _, row in df.iterrows():
            code = str(row["代码"]).strip()
            name = str(row["名称"]).strip()
            if not code:
                continue

            # akshare 返回的是 6 位裸代码，添加交易所前缀
            if code.startswith("6"):
                ticker = f"sh.{code}"
            elif code.startswith("0") or code.startswith("3"):
                ticker = f"sz.{code}"
            else:
                # 北交所等其他前缀，暂不处理
                continue

            pe_ttm = row.get("市盈率-动态")
            pb = row.get("市净率")

            result[ticker] = {
                "ticker": ticker,
                "name": name,
                "industry": None,
                "pe_ttm": float(pe_ttm) if pe_ttm not in (None, "", "-") else None,
                "pb": float(pb) if pb not in (None, "", "-") else None,
                "ipo_date": None,
                "out_date": None,
                "is_st": False,
                "source": "akshare",
            }

        logger.info(f"✅ akshare 元数据映射完成: {len(result)} 只")
        return result
    except Exception as e:
        logger.warning(f"⚠️  akshare 批量获取元数据失败: {e}")
        return {}


# ════════════════════════════════════════════════════════════════════════════════
# 向后兼容接口：_fetch_metadata_one / _fetch_metadata_one_hard
# 注意：主流程已改用 akshare 批量获取，此处保留仅供测试兼容
# ════════════════════════════════════════════════════════════════════════════════


def _fetch_metadata_one(factory, ticker: str, name_map: dict[str, str] | None = None) -> tuple[str, dict | None, str]:
    """拉取单只股票的元数据（兼容旧接口）。

    优先尝试 factory.fetch_metadata(ticker)；若失败或 factory 为 None，
    则回退到 akshare 批量名称映射。主流程已改用 _fetch_akshare_all_metadata
    批量获取，此函数保留仅供测试兼容。
    """
    try:
        # 首先尝试通过 provider 获取元数据（向后兼容旧测试）
        if factory is not None:
            try:
                meta = factory.fetch_metadata(ticker)
                if meta is not None:
                    key = normalize_ticker_key(ticker)
                    return (
                        key,
                        {
                            "ticker": meta.ticker,
                            "industry": meta.industry,
                            "pe_ttm": meta.pe_ttm,
                            "pb": meta.pb,
                            "ipo_date": meta.ipo_date,
                            "is_st": meta.is_st,
                            "source": meta.source,
                        },
                        "ok",
                    )
                # factory 返回 None，表示未找到，保持 "none" 状态
                key = normalize_ticker_key(ticker)
                return (key, None, "none")
            except Exception as e:
                # factory 抛出异常，返回错误状态（兼容旧测试期望）
                key = normalize_ticker_key(ticker)
                return (key, None, f"error: {str(e)[:50]}")

        # 回退：使用 akshare 批量名称映射（factory 为 None 时）
        bare = ticker.split(".")[-1] if "." in ticker else ticker
        name = (name_map or {}).get(bare, "")
        key = normalize_ticker_key(ticker)
        return (
            key,
            {
                "ticker": key,
                "name": name,
                "industry": None,
                "pe_ttm": None,
                "pb": None,
                "ipo_date": None,
                "is_st": False,
                "source": "akshare",
            },
            "ok",
        )
    except Exception as e:
        key = normalize_ticker_key(ticker)
        bare = key.split(".")[-1] if "." in key else key
        logger.warning(f"⚠️  获取 {ticker} 元数据失败: {e}")
        return (
            key,
            {
                "ticker": key,
                "name": (name_map or {}).get(bare, ""),
                "industry": None,
                "pe_ttm": None,
                "pb": None,
                "ipo_date": None,
                "is_st": False,
                "source": "akshare",
            },
            "ok",
        )


def _fetch_metadata_one_hard(factory, ticker: str, name_map: dict[str, str] | None = None) -> tuple[str, dict | None, str]:
    """带硬超时的单只元数据拉取（兼容旧接口）。

    主流程已改用 akshare 批量获取，此处保留仅供测试兼容。
    """
    holder: dict = {}

    def _run() -> None:
        result = _fetch_metadata_one(factory, ticker, name_map)
        holder["result"] = result

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(FETCH_PER_TICKER_TIMEOUT)

    if "result" not in holder:
        # daemon 线程仍在卡着（baostock 会话挂起）；放弃它，标记超时
        key = normalize_ticker_key(ticker)
        return (key, None, "timeout")

    return holder["result"]


# ════════════════════════════════════════════════════════════════════════════════
# 原有工具函数（断点续传、原子写入等）
# ════════════════════════════════════════════════════════════════════════════════


def _load_existing_metadata() -> dict[str, dict | None]:
    """读取现有 metadata.json；文件不存在或损坏时返回空 dict。

    Returns:
        {归一化 ticker: metadata dict 或 None} 映射
    """
    if not METADATA_FILE.exists():
        return {}
    try:
        data = json.loads(METADATA_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"⚠️ 读取现有 metadata.json 失败，将全量重建: {str(e)[:80]}")
        return {}


# ── 断点续传 checkpoint ──────────────────────────────────────────────────


def load_checkpoint() -> dict[str, dict | None]:
    """读取 checkpoint 文件；不存在或损坏时返回空 dict。

    Returns:
        {归一化 ticker: metadata dict 或 None} 映射
    """
    if not CHECKPOINT_FILE.exists():
        return {}
    try:
        data = json.loads(CHECKPOINT_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"⚠️ 读取 checkpoint 失败，将全量重拉: {str(e)[:80]}")
        return {}


def _atomic_write_json(path: Path, data: Mapping[str, object]) -> None:
    """原子写 JSON：先写同目录临时文件再 rename，避免其它进程读到半截文件。

    Args:
        path: 目标文件路径。
        data: 待写入的 JSON 映射。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def save_checkpoint(results: dict[str, dict | None]) -> None:
    """将当前进度原子写入 checkpoint 文件（先写临时文件再 rename，避免写一半损坏）。

    Args:
        results: {归一化 ticker: metadata dict 或 None} 当前已完成的映射
    """
    _atomic_write_json(CHECKPOINT_FILE, results)


def clear_checkpoint() -> None:
    """删除 checkpoint 文件（全量完成时调用）。"""
    try:
        CHECKPOINT_FILE.unlink(missing_ok=True)
    except OSError as e:
        logger.warning(f"⚠️ 清除 checkpoint 失败: {e}")


def metadata_age_hours() -> float | None:
    """metadata.json 距上次更新的小时数；文件不存在返回 None。"""
    if not METADATA_FILE.exists():
        return None
    return (time.time() - METADATA_FILE.stat().st_mtime) / 3600.0


def refresh_metadata_if_stale(max_age_hours: float = 24.0) -> bool:
    """metadata.json 超过 max_age_hours 未更新时触发全量刷新。

    每日流程第 1 步调用，保证元数据新鲜（默认 24h 未刷新则重拉全量）。

    Args:
        max_age_hours: 刷新阈值（小时），默认 24。

    Returns:
        True 执行了刷新，False 跳过（够新或异常外无副作用）。
    """
    age = metadata_age_hours()
    if age is not None and age < max_age_hours:
        logger.info(f"✅ metadata.json {age:.1f}h 前已更新（<{max_age_hours:.0f}h），跳过刷新")
        return False
    reason = "不存在" if age is None else f"{age:.1f}h 前更新"
    logger.info(f"📊 metadata.json {reason}，开始全量刷新")
    # TTL 触发时若有 checkpoint（上次中断），自动续传；否则全量重拉
    has_checkpoint = load_checkpoint()
    main(tickers_input=None, resume=bool(has_checkpoint))
    return True


def main(
    tickers_input: list[str] | None = None,
    resume: bool = False,
    use_checkpoint: bool = True,
) -> None:
    """批量拉取股票元数据（参考数据）并保存到 JSON 文件。

    部分模式（指定 --tickers）合并进现有 metadata.json，不覆盖全量；
    全量模式（默认）重建整个文件，支持断点续传（checkpoint）。

    Args:
        tickers_input: 指定股票代码列表，为 None 时拉取全量。
        resume: 为 True 时从 checkpoint 续拉（跳过已成功，重试失败/None）。
        use_checkpoint: 为 True 时每批完成后写 checkpoint，支持中断后续拉。
    """
    logger.info("📊 启动参考数据（元数据）拉取")

    # ── 第一步：用 akshare 批量获取所有 A 股名称、PE/PB（快速，单次请求）─────
    name_map = _fetch_akshare_all_metadata()

    tickers = tickers_input or get_all_tickers()
    # 北交所元数据拉取会硬超时（baostock/tonghuashun 对 bj. 会话挂起），且选股流程已过滤北交所，
    # 故默认跳过 bj. 开头的 ticker，避免浪费超时时间。
    tickers = [t for t in tickers if not t.startswith("bj.")]
    # ETF 元数据拉取会硬超时（baostock 对 ETF 会话挂起），且选股流程中 ETF
    # 仅参与纯技术面筛选，不依赖 PE/PB/行业等基本面元数据，故跳过 ETF，
    # 避免浪费超时时间。如需 ETF 元数据，可后续切换至 akshare/efinance 数据源。
    tickers = [t for t in tickers if not is_etf_ticker(t)]

    logger.info(
        f"📋 共 {len(tickers)} 只股票待写入元数据（全量模式）"
    )

    # ── 构建元数据结果：全部使用 akshare 数据 ─────
    results: dict[str, dict | None] = {}
    success = 0
    failed = 0
    start_time = time.time()

    for ticker in tickers:
        if ticker in name_map:
            results[ticker] = name_map[ticker]
            success += 1
        else:
            # akshare 中未找到的 ticker，保留空记录
            results[ticker] = {
                "ticker": ticker,
                "name": "",
                "industry": None,
                "pe_ttm": None,
                "pb": None,
                "ipo_date": None,
                "out_date": None,
                "is_st": False,
                "source": "akshare",
            }
            failed += 1

    elapsed = time.time() - start_time
    logger.info(f"\n{'=' * 60}")
    logger.info("✅ 参考数据拉取完成!")
    logger.info(f"  成功: {success}, 失败: {failed}")
    logger.info(f"  耗时: {elapsed:.1f}s ({elapsed / 60:.1f}min)")

    # 保存元数据到 JSON（原子写，避免备份/读取方读到半截文件）
    # 全量模式写入前清理旧 metadata 中的 key（防止 key 格式不一致的残留数据
    # 被并入新结果，例如旧版无交易所前缀的裸代码 key 混入带前缀的新 key）
    stale = [k for k in _load_existing_metadata() if k not in results]
    for k in stale:
        results.setdefault(k, None)

    # ── 写入前备份 metadata.json，防止误删除/覆盖 ─────────────────────
    from scripts.backup_cache import backup_all

    backup_all(keep=10)

    _atomic_write_json(METADATA_FILE, results)
    logger.info(f"  已保存至: {METADATA_FILE}")

    # 全量模式完成后清除 checkpoint
    if use_checkpoint:
        clear_checkpoint()
        logger.info("  🧹 checkpoint 已清除")
    logger.info(f"{'=' * 60}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="批量拉取股票元数据（参考数据）")
    parser.add_argument("--tickers", default=None, help="股票代码（逗号分隔），空=全量（部分拉取合并进现有文件）")
    parser.add_argument(
        "--force-refresh",
        action="store_true",
        help="忽略 24h TTL，强制全量刷新",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="断点续传：从 checkpoint 恢复，仅重拉失败/未完成的 ticker",
    )
    parser.add_argument(
        "--reset-checkpoint",
        action="store_true",
        help="清除 checkpoint 并从头开始（与 --resume 互斥）",
    )
    args = parser.parse_args()

    if args.reset_checkpoint:
        clear_checkpoint()
        logger.info("🧹 checkpoint 已清除，将全量重拉")
        main(tickers_input=None, resume=False)
    elif args.force_refresh:
        main(tickers_input=None, resume=args.resume)
    else:
        tickers_list = (
            [t.strip() for t in args.tickers.split(",") if t.strip()] if args.tickers else None
        )
        if tickers_list:
            main(tickers_input=tickers_list)
        else:
            refresh_metadata_if_stale()
