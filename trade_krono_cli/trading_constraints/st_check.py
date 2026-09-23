"""trading_constraints.st_check — ST/*ST 标的识别。"""

from __future__ import annotations

import re

from loguru import logger

from trade_krono_cli.constraints_config import ConstraintConfig
from trade_krono_cli.utils.st_cache import cached

# baostock ST 股票名称中常见的标记（实际需查询属性字段）
_ST_PATTERNS = re.compile(r"^(ST|\*ST|SST|N ST)", re.IGNORECASE)

# 模块级 ST 缓存由 @cached 装饰器管理（30 分钟 TTL）


def _is_st_by_name(ticker: str, name_hint: str | None = None) -> bool:
    """通过股票代码后缀或名称线索判断 ST。

    baostock 的 ST 标记通常在 name 字段中，这里先按 ticker 后三位做启发式
    判断（实际应在 fetch 数据后检查 name 字段）。
    """
    if name_hint:
        return bool(_ST_PATTERNS.search(name_hint))
    # 无法仅凭 ticker 判断，返回 False（后续通过 query 确认）
    return False


@cached(ttl=1800)
def check_st_status(
    ticker: str,
    config: ConstraintConfig | None = None,
) -> bool:
    """检查是否为 ST/*ST 标的。

    实现方式：通过 BaostockProvider（统一会话管理 + 线程安全锁）获取 ST 状态，
    缓存结果避免重复网络请求。

    Parameters
    ----------
    ticker : 股票代码（如 sh.600519）
    config : 约束配置

    Returns
    -------
    True 表示是 ST 标的，应被过滤

    """
    if config is None or not config.enable_st_filter:
        return False

    try:
        from trade_krono_cli.data_providers.baostock_provider import BaostockProvider

        provider = BaostockProvider()
        result = provider.check_st_status(ticker)
    except (ImportError, RuntimeError) as e:
        logger.debug(f"ST 检测初始化失败 {ticker}: {e}")
        result = False
    except Exception as e:
        logger.debug(f"ST 检测异常 {ticker}: {str(e)[:200]}")
        result = False

    if result:
        logger.info(f"🚫 {ticker} 被识别为 ST 标的，已过滤")
    return result
