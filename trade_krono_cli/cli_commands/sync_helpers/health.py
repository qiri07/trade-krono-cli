"""sync_helpers.health — Provider 健康检查与动态可用列表管理。"""

from __future__ import annotations

from loguru import logger
from rich.console import Console

from trade_krono_cli.cli_commands.sync_helpers.semaphore import (
    _HEALTH_REMOVE_THRESHOLD,
    _HEALTH_RESTORE_THRESHOLD,
)

console = Console()


def _check_provider_health() -> dict[str, bool]:
    """检查所有 Provider 的健康状态，返回 {name: is_healthy}。"""
    from trade_krono_cli.data_providers import get_data_factory

    factory = get_data_factory()
    result: dict[str, bool] = {}
    for name in factory.provider_chain:
        provider = factory.get_provider(name)
        if provider is None:
            result[name] = False
            continue
        try:
            result[name] = provider.health_check()
        except Exception as e:
            logger.debug(f"provider {name} health check 失败: {e}")
            result[name] = False
    return result


def _update_usable_providers(
    usable_providers: list[str],
    locked_providers: list[str],
    failure_counts: dict[str, int],
) -> list[str]:
    """重新检测 Provider 健康状态，动态更新可用列表（带滞环防抖）。

    使用连续失败/成功次数阈值，避免间歇性抖动导致 Provider 频繁切换。
    返回新的 usable_providers 列表，若与健康状态变化则打印日志。
    """
    health = _check_provider_health()
    from trade_krono_cli.data_providers.factory import get_data_factory

    factory = get_data_factory()
    ranked = factory.get_ranked_chain_for_ticker("sh.600519")
    new_providers: set[str] = set(locked_providers)  # 基于当前可用列表修改

    for p in ranked:
        is_healthy = health.get(p, False)
        if p in locked_providers:
            # 当前可用：连续失败才剔除
            if is_healthy:
                failure_counts[p] = 0
            else:
                failure_counts[p] = failure_counts.get(p, 0) + 1
                if failure_counts[p] >= _HEALTH_REMOVE_THRESHOLD:
                    new_providers.discard(p)
                    logger.warning(f"Provider {p} 连续 {failure_counts[p]} 次健康检查失败，已剔除")
        else:
            # 当前不可用：连续成功才恢复
            if is_healthy:
                failure_counts[p] = failure_counts.get(p, 0) + 1
                if failure_counts[p] >= _HEALTH_RESTORE_THRESHOLD:
                    new_providers.add(p)
                    logger.info(
                        f"Provider {p} 连续 {_HEALTH_RESTORE_THRESHOLD} 次健康检查通过，已恢复"
                    )
            else:
                failure_counts[p] = 0

    result = sorted(new_providers)
    if result != locked_providers:
        logger.info(f"Provider 状态变化: {locked_providers} → {result}")
        console.print(
            f"[dim]⚡ Provider 更新: {[p + ('✅' if p in result else '❌') for p in ranked]}[/dim]"
        )
    return result
