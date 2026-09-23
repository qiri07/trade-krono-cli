"""artifact_manifest.lock — artifact.lock 文件读写。"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from .types import ArtifactManifest

_ARTIFACT_LOCK_FILENAME = "artifact.lock"


def _artifact_lock_path(project_root: Path | None = None) -> Path:
    root = project_root or Path(__file__).resolve().parent.parent
    return root / "external" / _ARTIFACT_LOCK_FILENAME


def load_artifact_lock(project_root: Path | None = None) -> list[dict]:
    """加载 artifact.lock，返回条目列表（旧格式兼容）。"""
    lock_path = _artifact_lock_path(project_root)
    if not lock_path.exists():
        return []
    try:
        with open(lock_path, encoding="utf-8") as f:
            data = json.load(f)
        # 支持两种格式：数组 或 {"entries": [...]}
        if isinstance(data, list):
            return data
        return data.get("entries", [])
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"⚠️  artifact.lock 读取失败: {e}")
        return []


def save_artifact_lock(
    entries: list[dict],
    project_root: Path | None = None,
) -> Path:
    """保存 artifact.lock（追加模式：先 load，append，再 save）。"""
    lock_path = _artifact_lock_path(project_root)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    existing = load_artifact_lock(project_root)
    combined = existing + entries
    with open(lock_path, "w", encoding="utf-8") as f:
        json.dump({"schema_version": "2.0", "entries": combined}, f, indent=2, ensure_ascii=False)
    logger.debug(f"💾 artifact.lock 已更新: {len(combined)} 条记录")
    return lock_path


def append_artifact(
    manifest: ArtifactManifest,
    experiment_id: str | None = None,
    run_id: str | None = None,
    job_id: str | None = None,
    project_root: Path | None = None,
) -> dict:
    """将一次实验的 artifact 追加到 artifact.lock。

    Returns
    -------
    dict : 写入的条目（含 experiment_id）

    """
    eid = experiment_id or manifest.experiment_id()
    entry = {
        "experiment_id": eid,
        "run_id": run_id,
        "job_id": job_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "manifest": manifest.to_dict(),
        "summary": manifest.summary(),
    }
    save_artifact_lock([entry], project_root)
    return entry


def lookup_experiment(experiment_id: str, project_root: Path | None = None) -> dict | None:
    """按 experiment_id 查找历史记录。"""
    for entry in load_artifact_lock(project_root):
        if entry.get("experiment_id") == experiment_id:
            return entry
    return None
