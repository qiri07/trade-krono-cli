"""
CLI repo 子命令 — 外部项目管理（TradingAgents-astock、Kronos 等下游依赖）。
"""
from __future__ import annotations

from rich.console import Console
from rich.table import Table

console: Console = Console()


def repo_status() -> None:
    """查看所有外部 repo 的状态（分支、commit、dirty、pinned、lock 漂移）。"""
    from trade_krono_cli.external import status
    entries = status()
    if not entries:
        console.print("[yellow]⚠️  未检测到外部 repo 配置[/yellow]")
        return

    table = Table(title="📦 外部 Repo 状态", header_style="bold cyan")
    for col in ("Repo", "路径", "分支", "Commit", "Pinned", "Locked", "Dirty", "状态"):
        table.add_column(col, justify="left" if col in ("Repo", "路径", "状态") else "center")
    for e in entries:
        path_str = str(e.path_exists)
        branch = e.branch or "?"
        commit = e.commit_short or (e.commit[:12] if e.commit else "?")
        pinned = "✅" if e.is_pinned else "—"
        locked = "📌" if e.is_locked else "—"
        dirty = "⚠️" if e.is_dirty else "—"
        if not e.path_exists:
            state = "[red]不存在[/red]"
        elif not e.is_git_repo:
            state = "[yellow]非 git[/yellow]"
        elif e.lock_mismatch:
            state = "[red]lock漂移[/red]"
        elif e.error:
            state = f"[red]{e.error}[/red]"
        elif e.is_up_to_date is True:
            state = "[green]最新[/green]"
        elif e.is_up_to_date is False:
            state = "[yellow]落后[/yellow]"
        else:
            state = "—"
        table.add_row(e.name, path_str, branch, commit, pinned, locked, dirty, state)
    console.print(table)


def repo_doctor() -> None:
    """诊断外部 repo 问题，列出所有需要关注的项。"""
    from trade_krono_cli.external import doctor, load_lock, status
    issues = doctor()
    entries = status()
    lock = load_lock()

    if not issues and entries:
        console.print("[green]✅ 所有外部 repo 状态正常[/green]")
        for e in entries:
            if e.is_pinned and e.commit:
                console.print(f"  📌 [{e.name}] pinned → {e.commit[:12]}")
            elif e.is_locked and e.lock_commit:
                console.print(
                    f"  🔒 [{e.name}] locked  → {e.lock_commit}"
                    "（未 pinned，跟踪 branch）"
                )
            elif e.branch:
                console.print(f"  🌿 [{e.name}] tracking → {e.branch}")
        if lock.get("generated_at"):
            console.print(
                f"\n  [dim]repo.lock 最后更新: {lock['generated_at']}[/dim]"
            )
        return

    if not entries:
        console.print("[yellow]⚠️  未检测到外部 repo 配置[/yellow]")
        console.print("  建议：创建 external/repos.yaml 或使用默认路径")
        raise SystemExit(1)

    console.print("[bold red]❌ 检测到以下问题：[/bold red]")
    for issue in issues:
        console.print(f"  {issue}")

    console.print("\n[dim]💡 修复建议：[/dim]")
    console.print("  • 路径不存在  → 将项目 clone 到指定路径，或编辑 external/repos.yaml")
    console.print("  • 非 git repo → 初始化 git：git init")
    console.print("  • dirty       → git stash 或 git checkout -- .")
    console.print("  • lock 漂移   → 运行 repo pin <name> <commit> 重新锁定")
    console.print("  • 落后于远程  → 运行 repo update")
    raise SystemExit(1)


def repo_update() -> None:
    """拉取所有外部 repo 的最新代码（仅 unpinned repos），并刷新 repo.lock。"""
    from trade_krono_cli.external import get_repos, update
    repos = get_repos()
    pinned = [r.name for r in repos if r.commit]
    if pinned:
        console.print(
            f"[yellow]⚠️  以下 repo 已 pinned，跳过 update："
            f"{', '.join(pinned)}[/yellow]"
        )
        console.print("  （pinned repo 需手动 git checkout 后再 update）")

    results = update()
    for msg in results.values():
        console.print(f"  {msg}")


def repo_pin(
    name: str | None = None,  # typer will inject via decorator
    commit: str | None = None,
) -> None:
    """将外部 repo pin 到指定 commit，同时更新 repos.yaml 和 repo.lock。

    示例：
      trade-krono-cli repo pin tradingagents abc1234
      trade-krono-cli repo pin kronos def5678
    """
    from trade_krono_cli.external import pin
    if name is None or commit is None:
        console.print("[red]❌ --name 和 --commit 均为必填参数[/red]")
        raise SystemExit(1)
    try:
        pin(name, commit)
        console.print(f"[green]✅ [{name}] 已 pin 到 {commit[:12]}[/green]")
        console.print("   配置文件已更新：external/repos.yaml + external/repo.lock")
    except ValueError as e:
        console.print(f"[red]❌ {e}[/red]")
        raise SystemExit(1)
