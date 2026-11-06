#!/usr/bin/env python3
"""核心数据备份脚本 — 在数据写入前自动备份 pipeline_cache.db 与 metadata.json。

设计要点：
  · SQLite 走官方在线备份 API（一致性快照）+ 原子 rename 落地。直接 copy2 一个正在
    写入的 WAL 库，可能得到「缺最近事务」或「页面撕裂」的损坏副本。
  · 备份文件名固定为 `<stem>_YYYYMMDD_HHMMSS.<ext>`；清理时只删这一命名格式，
    人工命名的历史快照（如 `pipeline_cache_pre1500clean_*.db`）永不被自动删除。
  · 每个文件族（pipeline_cache / metadata）独立保留 keep 份，互不挤占名额。
  · 源文件自上次备份后未变化时跳过拷贝，避免每日流程反复复制 ~1.1 GB 大库。

用法：
  uv run python scripts/backup_cache.py                 # 备份全部核心数据
  uv run python scripts/backup_cache.py --keep 5        # 每类保留 5 份（默认 10）
  uv run python scripts/backup_cache.py --force         # 忽略「未变化」判断，强制备份
  uv run python scripts/backup_cache.py --check-only    # 只查看备份状态，不备份
  uv run python scripts/backup_cache.py --check-only --verify  # 额外校验最新备份完整性
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

from loguru import logger

# 确保仓库根在 sys.path 中（供直接 python scripts/backup_cache.py 使用）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts._utils import get_sqlite_conn

DEFAULT_KEEP = 10
TIMESTAMP_FMT = "%Y%m%d_%H%M%S"

# 仅匹配本脚本生成的备份名：<stem>_YYYYMMDD_HHMMSS[_ffffff].<ext>
_BACKUP_NAME_RE = re.compile(r"^(?P<stem>.+)_(?P<ts>\d{8}_\d{6}(?:_\d{6})?)\.(?P<suffix>db|json)$")

# 文件族：备份名前缀 → 源文件名
_FAMILIES: dict[str, str] = {
    "pipeline_cache": "pipeline_cache.db",
    "metadata": "metadata.json",
}


class BackupError(Exception):
    """备份过程失败（源文件非法、快照校验不通过等）。"""


def get_cache_dir() -> Path:
    """返回缓存目录（兼容测试隔离：优先读 Settings.cache_dir）。

    Returns:
        缓存目录，读取 Settings 失败时回退到 `outputs/cache`。
    """
    try:
        from trade_krono_cli.config import get_settings  # noqa: PLC0415

        return Path(get_settings().cache_dir)
    except (ImportError, OSError, RuntimeError, ValueError):
        return Path("outputs/cache")


def get_backup_dir() -> Path:
    """返回备份目录（`<cache_dir>/backups`）。"""
    return get_cache_dir() / "backups"


def list_backups(backup_dir: Path, stem: str) -> list[Path]:
    """按文件名时间戳倒序列出某文件族的备份。

    使用文件名时间戳而非 mtime 排序：`copy2` 会保留源文件 mtime，mtime 不能
    代表备份创建时间。

    Args:
        backup_dir: 备份目录。
        stem: 文件族前缀（如 `pipeline_cache`）。

    Returns:
        该文件族的备份路径列表，最新的在前。
    """
    if not backup_dir.exists():
        return []
    items: list[tuple[str, Path]] = []
    for path in backup_dir.glob(f"{stem}_*"):
        if not path.is_file():
            continue
        match = _BACKUP_NAME_RE.match(path.name)
        if match is not None and match.group("stem") == stem:
            items.append((match.group("ts"), path))
    items.sort(key=lambda item: item[0], reverse=True)
    return [path for _, path in items]


def _latest_backup(backup_dir: Path, stem: str) -> Path | None:
    """返回某文件族最新的备份，没有则返回 None。"""
    backups = list_backups(backup_dir, stem)
    return backups[0] if backups else None


def _is_up_to_date(src: Path, backup_dir: Path) -> Path | None:
    """判断源文件自上次备份后是否未变化。

    判据：最新备份的 mtime >= 源文件 mtime。SQLite 在线备份的文件 mtime 为创建时间，
    JSON 用 copy2 保留了源 mtime，两种命名在此判据下都成立。

    Args:
        src: 源文件。
        backup_dir: 备份目录。

    Returns:
        未变化时返回最新备份路径，否则返回 None。
    """
    latest = _latest_backup(backup_dir, src.stem)
    if latest is None:
        return None
    try:
        if latest.stat().st_mtime >= src.stat().st_mtime:
            return latest
    except OSError:
        return None
    return None


def _next_backup_path(backup_dir: Path, stem: str, suffix: str) -> Path:
    """生成不与现有文件冲突的备份路径（同秒多次备份时追加微秒）。"""
    base = datetime.now().strftime(TIMESTAMP_FMT)
    candidate = backup_dir / f"{stem}_{base}{suffix}"
    if not candidate.exists():
        return candidate
    micros = datetime.now().strftime("%f")
    return backup_dir / f"{stem}_{base}_{micros}{suffix}"


def _backup_sqlite(src: Path, dst: Path) -> None:
    """用 SQLite 在线备份 API 生成一致性快照并校验完整性。

    Args:
        src: 源数据库路径。
        dst: 目标备份路径（调用方保证其位于目标目录，便于原子 rename）。

    Raises:
        sqlite3.Error: 备份或完整性校验失败。
    """
    tmp = dst.with_name(f"{dst.name}.tmp")
    tmp.unlink(missing_ok=True)
    src_conn = get_sqlite_conn(src)
    try:
        dst_conn = sqlite3.connect(str(tmp))
        try:
            src_conn.backup(dst_conn)
            row = dst_conn.execute("PRAGMA integrity_check").fetchone()
            if not row or row[0] != "ok":
                msg = f"备份完整性校验失败: {row}"
                raise sqlite3.DatabaseError(msg)
        finally:
            dst_conn.close()
        os.replace(tmp, dst)
    finally:
        src_conn.close()
        tmp.unlink(missing_ok=True)


def _backup_json(src: Path, dst: Path) -> None:
    """备份 JSON 文件，写入前校验源文件是合法 JSON。

    Args:
        src: 源文件。
        dst: 目标备份路径。

    Raises:
        BackupError: 源文件不是合法 JSON（例如正在被其它进程覆盖写）。
        OSError: 读取或拷贝失败。
    """
    try:
        json.loads(src.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        msg = f"源文件不是合法 JSON，拒绝备份: {exc}"
        raise BackupError(msg) from exc

    tmp = dst.with_name(f"{dst.name}.tmp")
    tmp.unlink(missing_ok=True)
    try:
        shutil.copy2(src, tmp)
        os.replace(tmp, dst)
    finally:
        tmp.unlink(missing_ok=True)


def backup_file(
    src: Path,
    backup_dir: Path,
    *,
    force: bool = False,
    skip_if_unchanged: bool = True,
) -> Path | None:
    """备份单个文件到备份目录。

    Args:
        src: 源文件路径。
        backup_dir: 备份目录。
        force: 为 True 时忽略「源文件未变化」判断，强制生成新快照。
        skip_if_unchanged: 为 True 时，源文件自上次备份后未变化则跳过拷贝。

    Returns:
        备份文件路径（跳过时返回已存在的最新备份）；源文件缺失或备份失败返回 None。
    """
    if not src.exists():
        logger.warning(f"⚠️  源文件不存在，跳过: {src}")
        return None

    if skip_if_unchanged and not force:
        latest = _is_up_to_date(src, backup_dir)
        if latest is not None:
            logger.info(f"⏭️  {src.name} 自上次备份后未变化，跳过（{latest.name}）")
            return latest

    backup_dir.mkdir(parents=True, exist_ok=True)
    dst = _next_backup_path(backup_dir, src.stem, src.suffix)

    try:
        if src.suffix == ".db":
            _backup_sqlite(src, dst)
        else:
            _backup_json(src, dst)
    except (OSError, sqlite3.Error, BackupError) as exc:
        logger.error(f"❌ 备份失败 {src}: {exc}")
        return None

    size_mb = dst.stat().st_size / 1024 / 1024
    logger.info(f"✅ 已备份 {src.name} -> {dst.name} ({size_mb:.1f} MB)")
    return dst


def prune_backups(
    backup_dir: Path,
    keep: int,
    *,
    stems: tuple[str, ...] = tuple(_FAMILIES),
) -> list[Path]:
    """按文件族分别清理旧备份，只删本脚本生成的备份名。

    Args:
        backup_dir: 备份目录。
        keep: 每个文件族保留的最近备份数量（<=0 表示不清理）。
        stems: 需要清理的文件族前缀。

    Returns:
        被删除的备份路径列表。
    """
    if keep <= 0:
        return []

    deleted: list[Path] = []
    for stem in stems:
        for old in list_backups(backup_dir, stem)[keep:]:
            try:
                old.unlink()
            except OSError as exc:
                logger.warning(f"⚠️  清理旧备份失败 {old.name}: {exc}")
                continue
            logger.info(f"🗑️  清理旧备份 {old.name}")
            deleted.append(old)
    return deleted


def backup_all(
    keep: int = DEFAULT_KEEP,
    *,
    force: bool = False,
    skip_if_unchanged: bool = True,
) -> dict[str, Path | None]:
    """备份全部核心数据文件并清理旧备份。

    Args:
        keep: 每个文件族保留的最近备份数量（0 表示不清理）。
        force: 为 True 时忽略「源文件未变化」判断。
        skip_if_unchanged: 为 True 时跳过未变化的源文件。

    Returns:
        备份结果字典 {源文件名: 备份路径或 None}。
    """
    cache_dir = get_cache_dir()
    backup_dir = cache_dir / "backups"

    logger.info(f"📦 开始备份数据文件（目标目录: {backup_dir}）")
    results: dict[str, Path | None] = {}
    for filename in _FAMILIES.values():
        src = cache_dir / filename
        results[filename] = backup_file(
            src,
            backup_dir,
            force=force,
            skip_if_unchanged=skip_if_unchanged,
        )

    prune_backups(backup_dir, keep)

    success = sum(1 for path in results.values() if path is not None)
    logger.info(f"✅ 备份完成: {success}/{len(results)} 个文件")
    return results


def check_status(*, verify: bool = False) -> int:
    """打印源文件与备份状态，不执行备份。

    Args:
        verify: 为 True 时对最新的 pipeline_cache 备份做 `PRAGMA integrity_check`。

    Returns:
        0 表示源文件齐全，1 表示存在缺失的核心数据文件。
    """
    cache_dir = get_cache_dir()
    backup_dir = cache_dir / "backups"
    missing = 0

    logger.info("📊 数据备份状态检查")
    logger.info("=" * 60)
    for filename in _FAMILIES.values():
        src = cache_dir / filename
        if src.exists():
            size_mb = src.stat().st_size / 1024 / 1024
            mtime = datetime.fromtimestamp(src.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            logger.info(f"  📁 {filename}: {size_mb:.1f} MB (最后修改: {mtime})")
        else:
            missing += 1
            logger.info(f"  ❌ {filename}: 文件不存在")

    logger.info("")
    entries: list[Path] = []
    for stem in _FAMILIES:
        entries.extend(list_backups(backup_dir, stem))
    entries.sort(key=lambda p: p.stat().st_mtime, reverse=True)

    if entries:
        logger.info(f"📦 备份文件（共 {len(entries)} 个）：")
        for path in entries[:20]:
            size_mb = path.stat().st_size / 1024 / 1024
            logger.info(f"  {path.name}: {size_mb:.1f} MB")
        if len(entries) > 20:
            logger.info(f"  ... 还有 {len(entries) - 20} 个备份文件")
    else:
        logger.info("  📦 无备份文件")

    if verify:
        latest_db = _latest_backup(backup_dir, "pipeline_cache")
        if latest_db is None:
            logger.info("  ⚠️  无 DB 备份可校验")
        else:
            logger.info("")
            logger.info(f"🔍 校验最新备份: {latest_db.name}")
            try:
                conn = get_sqlite_conn(latest_db, readonly=True)
                try:
                    row = conn.execute("PRAGMA integrity_check").fetchone()
                finally:
                    conn.close()
                if row and row[0] == "ok":
                    logger.info("  ✅ integrity_check: ok")
                else:
                    missing += 1
                    logger.info(f"  ❌ integrity_check: {row}")
            except sqlite3.Error as exc:
                missing += 1
                logger.info(f"  ❌ integrity_check 执行失败: {exc}")

    return 0 if missing == 0 else 1


def main(argv: list[str] | None = None) -> int:
    """命令行入口。

    Args:
        argv: 命令行参数（默认取 sys.argv[1:]）。

    Returns:
        0 表示全部成功，1 表示存在失败。
    """
    parser = argparse.ArgumentParser(description="核心数据备份脚本")
    parser.add_argument(
        "--keep",
        type=int,
        default=DEFAULT_KEEP,
        help=f"每个文件族保留的备份数量（默认 {DEFAULT_KEEP}，0 表示不限制）",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="忽略「源文件未变化」判断，强制生成新快照",
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="只检查备份状态，不执行备份",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="配合 --check-only：校验最新 DB 备份的完整性",
    )
    args = parser.parse_args(argv)

    if args.check_only:
        return check_status(verify=args.verify)

    results = backup_all(args.keep, force=args.force)
    cache_dir = get_cache_dir()
    # 源文件不存在不算失败（首次运行尚无 DB），只有「文件在但备份失败」才返回非 0
    failed = [
        name for name, path in results.items() if path is None and (cache_dir / name).exists()
    ]
    if failed:
        logger.error(f"❌ 备份失败 {len(failed)} 个文件: {failed}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
