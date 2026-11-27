"""data_providers.factory — 数据源工厂 + 自动降级路由。

本模块为兼容层：核心职责已拆分至子模块，此处保留 `DataProviderFactory`
（Provider 实例管理、带降级的 K 线/行情/元数据/批量拉取）及模块级单例，
并从子模块重导出相关名称，保证现有 `from trade_krono_cli.data_providers.factory import ...`
不做任何改动即可继续工作。

子模块职责：
  - factory_circuit.ProviderCircuitBreaker：健康检查 + 连续失败熔断
  - factory_benchmark：Provider 自适应优先级 Benchmark + 排名缓存
  - factory_retry.FetchRetryPolicy / fetch_kline_with_retry：带指数退避重试
  - factory_providers.ProviderInstanceManager：Provider 懒加载与缓存
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Any

from loguru import logger

from trade_krono_cli.config import get_settings
from trade_krono_cli.data_providers.base import StockMetadata
from trade_krono_cli.data_providers.factory_benchmark import (
    bench_all_providers,
    get_cached_ranked_chain_factory,
    get_ranked_chain_for_ticker_factory,
    invalidate_rank_cache_factory,
    write_ranked_chain_factory,
)
from trade_krono_cli.data_providers.factory_circuit import (
    ProviderCircuitBreaker,
)
from trade_krono_cli.data_providers.factory_providers import (
    ProviderInstanceManager,
)
from trade_krono_cli.data_providers.factory_retry import (
    FetchRetryPolicy,
    fetch_kline_with_retry,
)

if TYPE_CHECKING:
    from trade_krono_cli.data_providers.base import (
        DataProvider,
        KlineData,
        RealtimeQuote,
    )

# 向后兼容：_BenchResult 供测试直接 import（运行时可用）
from trade_krono_cli.data_providers.benchmark import _BenchResult  # noqa: TC001

# 导入限流器和重试装饰器（向后兼容 re-export）
from trade_krono_cli.data_providers.rate_limiter import RateLimiter  # noqa: F401

# ════════════════════════════════════════════════════════
#  工厂实现（核心：Provider 实例管理 + 带降级的拉取）
# ════════════════════════════════════════════════════════


class DataProviderFactory(ProviderInstanceManager):
    """数据源工厂，管理 Provider 实例生命周期并实现自动降级。

    设计要点：
      - 进程级单例缓存，避免重复创建 Provider
      - lazy import：只在首次使用时导入依赖包
      - 健康检查优先：尝试前检查 Provider 可用性（委托 ProviderCircuitBreaker）
      - 维度拆分：K 线 / 行情 / 元数据可来自不同源
    """

    # 支持的 Provider 名称 → 类映射（继承自 ProviderInstanceManager）
    _PROVIDER_REGISTRY: dict[str, type[DataProvider]] = {}

    # 进程级缓存（线程安全）（继承自 ProviderInstanceManager）
    _instance_cache: dict[str, DataProvider] = {}
    _cache_lock = threading.Lock()

    # 自适应优先级缓存：ticker_type → (ranked_chain, timestamp)
    _rank_cache: dict[str, tuple[list[str], float]] = {}
    _rank_lock = threading.Lock()

    # 全局限流器
    _rate_limiter = RateLimiter.get_instance()

    # 向后兼容：保留模块级熔断器引用（实际逻辑委托给 ProviderCircuitBreaker 实例）
    _circuit: ProviderCircuitBreaker | None = None
    _circuit_lock = threading.Lock()

    @staticmethod
    def _rank_cache_ttl_sec() -> int:
        """Provider 排名缓存 TTL（秒），从 Settings 读取。"""
        import trade_krono_cli.config as _cfg

        return _cfg.get_settings().provider_rank_cache_ttl_sec

    def __init__(self, primary: str = "baostock", fallbacks: list[str] | None = None) -> None:
        """初始化工厂。

        Parameters
        ----------
        primary : str
            主数据源名称，默认 "baostock"
        fallbacks : list[str] | None
            备用数据源列表，按优先级排列
        """
        self.primary = primary
        self.fallbacks = fallbacks or self._default_fallbacks()
        # 实例级熔断器
        self._circuit = ProviderCircuitBreaker()
        # 重试策略（可覆盖）
        self._retry_policy = FetchRetryPolicy()

    @staticmethod
    def _default_fallbacks() -> list[str]:
        """默认降级顺序：akshare → tencent → mootdx → tushare → tonghuashun → efinance → easyquotation → adk。

        tencent（腾讯财经）作为兜底补充源：不封 IP、覆盖沪深 A 股 / ETF / 沪深指数且带估值 / 市值 / 换手
        字段，主源不可达或缺少估值字段时自动降级使用。北交所由 tonghuashun 提供。adk（a-stock-data/adata）
        作为链尾兜底源。
        """
        return [
            "akshare",
            "tencent",
            "mootdx",
            "tushare",
            "tonghuashun",
            "efinance",
            "easyquotation",
            "adk",
        ]

    @property
    def provider_chain(self) -> list[str]:
        """返回按优先级排列的完整 Provider 链."""
        return [self.primary] + [f for f in self.fallbacks if f != self.primary]

    def _chain_for_ticker(self, ticker: str) -> list[str]:
        """根据 ticker 前缀返回本实例的最优 Provider 链。

        使用 ``self.primary`` / ``self.fallbacks``（而非全局单例），保证显式构造的
        工厂配置生效；优先使用自适应 benchmark 缓存结果（TTL 10 分钟），
        未缓存或过期时回退到固定顺序。
        北交所（bj.）股票 baostock/mootdx 均不支持，强制优先使用 tonghuashun。
        港股（hk.）股票由 akshare_hk 专属提供，akshare（A股）降级备用。
        """
        base_chain = self.provider_chain

        # 北交所特殊处理：tonghuashun 置顶
        if ticker.startswith("bj."):
            if "tonghuashun" in base_chain:
                base_chain.remove("tonghuashun")
            base_chain.insert(0, "tonghuashun")

        # 港股特殊处理：akshare_hk 置顶
        if ticker.startswith("hk."):
            if "akshare_hk" in base_chain:
                base_chain.remove("akshare_hk")
            base_chain.insert(0, "akshare_hk")

        # 尝试使用 cached benchmark 结果
        ticker_type = ticker.split(".", maxsplit=1)[0] if "." in ticker else ticker
        cached = self._get_cached_ranked_chain(ticker_type)
        if cached is not None:
            ranked_chain = cached[0]
            filtered = [p for p in ranked_chain if p in base_chain]
            extra = [p for p in base_chain if p not in filtered]
            return filtered + extra

        return base_chain

    @staticmethod
    def _provider_chain_for_ticker(ticker: str) -> list[str]:
        """向后兼容的静态入口（使用全局工厂单例）。

        新代码请用实例方法 :meth:`_chain_for_ticker`，避免自定义 primary/fallbacks
        被全局单例覆盖。
        """
        return get_data_factory()._chain_for_ticker(ticker)

    def _provider_healthy(self, name: str, provider: DataProvider) -> bool:
        """带短 TTL 缓存和连续失败熔断的 Provider 健康检查（委托 circuit breaker）。"""
        circuit = self._get_circuit()
        return circuit.is_provider_healthy(name, provider)

    def _record_provider_fail(self, name: str) -> None:
        """记录一次 Provider 调用失败，用于熔断计数。"""
        self._get_circuit().record_provider_fail(name)

    def _record_provider_success(self, name: str) -> None:
        """记录一次 Provider 调用成功，重置熔断计数。"""
        self._get_circuit().record_provider_success(name)

    def _get_circuit(self) -> ProviderCircuitBreaker:
        """获取（或创建）本实例的熔断器。"""
        if self._circuit is None:
            self._circuit = ProviderCircuitBreaker()
        return self._circuit

    # ── Provider 实例管理（继承自 ProviderInstanceManager）──────────────

    # get_provider / get_providers / available_providers / health_check_all
    # reset_cache / _get_provider_class 均继承自 ProviderInstanceManager

    # ── 核心接口：带降级的拉取方法 ─────────────────────────────────────

    def _fetch_kline_with_retry(
        self,
        provider: DataProvider,
        name: str,
        ticker: str,
        start_date: str,
        end_date: str,
        frequency: str,
        adjustflag: str,
        max_retries: int | None = None,
        base_delay: float | None = None,
        max_delay: float | None = None,
        retry_on: tuple[type[Exception], ...] = (ConnectionError, TimeoutError, OSError),
    ) -> KlineData | None:
        """对单个 Provider 带指数退避重试地拉取 K 线。

        重试耗尽后返回 None，由调用方决定是否降级到下一个 Provider。
        429 限流错误会触发 provider 冷却期，避免持续打击已限流的源。

        只有在 ``retry_on`` 指定的异常类型，或异常消息包含 ``429``/``rate limit``
        时才会重试；其他异常（如硬故障的 ``RuntimeError``）立即放弃并降级。
        这避免对明确不可恢复的错误做无谓重试，保持降级链路的响应速度。

        若 ``max_retries``/``base_delay``/``max_delay`` 为 None，则从全局 Settings
        中读取（对应环境变量 ``RATE_LIMIT_MAX_RETRIES`` /
        ``RATE_LIMIT_BASE_BACKOFF`` / ``RATE_LIMIT_MAX_BACKOFF``）。

        Returns
        -------
        KlineData | None
            成功返回数据，重试耗尽或空数据返回 None。
        """
        # 允许显式覆盖重试参数；否则使用实例级策略
        policy = self._retry_policy
        if max_retries is not None or base_delay is not None or max_delay is not None:
            policy = FetchRetryPolicy(
                max_retries=max_retries if max_retries is not None else policy.max_retries,
                base_delay=base_delay if base_delay is not None else policy.base_delay,
                max_delay=max_delay if max_delay is not None else policy.max_delay,
            )

        return fetch_kline_with_retry(
            provider=provider,
            name=name,
            ticker=ticker,
            start_date=start_date,
            end_date=end_date,
            frequency=frequency,
            adjustflag=adjustflag,
            policy=policy,
            rate_limiter=self._rate_limiter,
            record_fail=self._record_provider_fail,
            record_success=self._record_provider_success,
            record_429=self._rate_limiter.record_429,
        )

    def _retry_fetch(
        self,
        name: str,
        ticker: str,
        fetch_fn,
        op_label: str = "",
        max_retries: int | None = None,
        base_delay: float | None = None,
        max_delay: float | None = None,
    ):
        """对单个 Provider 调用带指数退避重试的通用封装。

        只重试网络相关异常（ConnectionError/TimeoutError/OSError）以及包含
        ``429``/``rate limit`` 的异常；硬故障立即放弃并返回 ``None``。

        重试参数缺省从 Settings 读取（环境变量 RATE_LIMIT_*），也可通过
        显式传入覆盖，便于测试使用极短退避时间。

        Args:
            name: Provider 名称（用于限流和熔断计数）。
            ticker: 股票代码。
            fetch_fn: 无参数可调用对象，执行实际的 Provider 拉取，返回结果或 None。
            op_label: 操作标签，用于日志输出（如 "行情"、"元数据"）。
            max_retries: 最大重试次数，缺省从 Settings 读取。
            base_delay: 基础退避秒数，缺省从 Settings 读取。
            max_delay: 最大退避秒数，缺省从 Settings 读取。

        Returns:
            Any | None: 成功返回 fetch_fn 的结果，重试耗尽或硬故障返回 None。
        """
        policy = self._retry_policy
        if max_retries is not None or base_delay is not None or max_delay is not None:
            policy = FetchRetryPolicy(
                max_retries=max_retries if max_retries is not None else policy.max_retries,
                base_delay=base_delay if base_delay is not None else policy.base_delay,
                max_delay=max_delay if max_delay is not None else policy.max_delay,
            )

        return FetchRetryPolicy.execute(
            policy,
            name=name,
            ticker=ticker,
            fetch_fn=fetch_fn,
            op_label=op_label,
            rate_limiter=self._rate_limiter,
            record_fail=self._record_provider_fail,
            record_success=self._record_provider_success,
            record_429=self._rate_limiter.record_429,
        )

    def fetch_kline(
        self,
        ticker: str,
        start_date: str,
        end_date: str,
        frequency: str = "d",
        adjustflag: str = "1",
    ) -> KlineData | None:
        """拉取 K 线数据，带限流和重试，按优先级尝试各 Provider，失败自动降级。

        北交所（bj.）股票由 baostock/mootdx 不支持，强制优先使用 tonghuashun。

        Returns
        -------
        KlineData | None
        """
        chain = self._chain_for_ticker(ticker)

        for name in chain:
            provider = self.get_provider(name)
            if provider is None:
                continue
            if not provider.supports_kline:
                logger.debug(f"{name}: 不支持 K 线，跳过")
                continue
            if not self._provider_healthy(name, provider):
                logger.debug(f"{name}: 健康检查未通过，跳过")
                continue

            # 获取限流许可，如果需要等待则先等待
            wait_time = self._rate_limiter.acquire(name)
            if wait_time > 0:
                logger.debug(f"⏳ {name} 限流等待 {wait_time:.1f}s: {ticker}")
                time.sleep(wait_time)

            # 带指数退避重试；重试耗尽后降级到下一个 provider
            data = self._fetch_kline_with_retry(
                provider, name, ticker, start_date, end_date, frequency, adjustflag
            )
            if data is not None:
                return data
        logger.warning(f"❌ 所有 Provider 均无法获取 K 线: {ticker}")
        return None

    def fetch_kline_from_provider(
        self,
        provider_name: str,
        ticker: str,
        start_date: str,
        end_date: str,
        frequency: str = "d",
        adjustflag: str = "1",
    ) -> KlineData | None:
        """从指定 Provider 拉取 K 线（带限流 + 指数退避重试），不做降级。

        用于「该市场只有唯一有效数据源」的场景（如港股 ``akshare_hk``），
        避免默认降级链对其他市场源发起无效请求。

        Args:
            provider_name: Provider 名称，如 ``"akshare_hk"``。
            ticker: 标准化 ticker，如 ``"hk.00700"``。
            start_date: 起始日期 ``YYYY-MM-DD``。
            end_date: 结束日期 ``YYYY-MM-DD``。
            frequency: K 线周期，默认日线。
            adjustflag: 复权方式，默认 ``"1"``（后复权）。

        Returns:
            :class:`KlineData`；Provider 不可用或无数据时返回 ``None``。
        """
        provider = self.get_provider(provider_name)
        if provider is None or not provider.supports_kline:
            logger.warning(f"{provider_name}: Provider 不可用或不支持 K 线")
            return None

        wait_time = self._rate_limiter.acquire(provider_name)
        if wait_time > 0:
            time.sleep(wait_time)

        return self._fetch_kline_with_retry(
            provider, provider_name, ticker, start_date, end_date, frequency, adjustflag
        )

    def fetch_quote(self, ticker: str) -> RealtimeQuote | None:
        """获取实时行情，按优先级尝试各 Provider。

        Returns
        -------
        RealtimeQuote | None
        """
        chain = self._chain_for_ticker(ticker)
        for name in chain:
            provider = self.get_provider(name)
            if provider is None:
                continue
            if not provider.supports_quote:
                continue
            if not self._provider_healthy(name, provider):
                continue
            # 获取限流许可
            wait_time = self._rate_limiter.acquire(name)
            if wait_time > 0:
                logger.debug(f"⏳ {name} 限流等待 {wait_time:.1f}s: {ticker} 行情")
                time.sleep(wait_time)

            # 带指数退避重试；重试耗尽后降级到下一个 provider
            quote = self._retry_fetch(
                name,
                ticker,
                lambda p=provider: p.fetch_quote(ticker),
                op_label="行情",
            )
            if quote is not None:
                return quote
            # 空数据不降级，直接返回 None
            logger.debug(f"{name} 返回空行情: {ticker}")
            return None
        return None

    def fetch_metadata(self, ticker: str) -> StockMetadata | None:
        """获取股票元数据，按优先级尝试各 Provider 并合并结果。

        策略：遍历所有 provider，收集各自非空字段，最终合并为一条完整元数据。
        同花顺提供 PE/PB/行业，baostock 提供 IPO 日期，互补无冲突。

        Returns
        -------
        StockMetadata | None
        """
        chain = self._chain_for_ticker(ticker)
        merged: dict[str, Any] = {"ticker": ticker}
        found_any = False
        for name in chain:
            provider = self.get_provider(name)
            if provider is None:
                continue
            if not provider.supports_metadata:
                continue
            if not self._provider_healthy(name, provider):
                continue
            # 获取限流许可
            wait_time = self._rate_limiter.acquire(name)
            if wait_time > 0:
                logger.debug(f"⏳ {name} 限流等待 {wait_time:.1f}s: {ticker} 元数据")
                time.sleep(wait_time)

            # 带指数退避重试；重试耗尽后降级到下一个 provider
            meta = self._retry_fetch(
                name,
                ticker,
                lambda p=provider: p.fetch_metadata(ticker),
                op_label="元数据",
            )
            if meta is None:
                continue
            found_any = True
            logger.debug(f"✅ 元数据获取成功: {ticker} ← {name}")
            # 合并非空字段（后面的 provider 覆盖前面的同名字段）
            if meta.pe_ttm is not None:
                merged["pe_ttm"] = meta.pe_ttm
            if meta.pb is not None:
                merged["pb"] = meta.pb
            if meta.industry is not None:
                merged["industry"] = meta.industry
            if meta.ipo_date is not None and merged.get("ipo_date") is None:
                merged["ipo_date"] = meta.ipo_date
            if meta.out_date is not None and merged.get("out_date") is None:
                merged["out_date"] = meta.out_date
            if meta.is_st and not merged.get("is_st"):
                merged["is_st"] = meta.is_st
            if meta.source is not None and (meta.pe_ttm is not None or meta.pb is not None):
                merged["source"] = meta.source
        if not found_any:
            return None
        return StockMetadata(**merged)

    def fetch_merged(
        self,
        ticker: str,
        start_date: str,
        end_date: str,
        frequency: str = "d",
        adjustflag: str = "1",
    ) -> dict:
        """从不同 Provider 分别获取 K 线 / 行情 / 元数据，合并返回。

        每个维度独立降级，确保最大可用性。

        Returns
        -------
        dict with keys: kline, quote, metadata
        """
        kline = self.fetch_kline(ticker, start_date, end_date, frequency, adjustflag)
        quote = self.fetch_quote(ticker)
        metadata = self.fetch_metadata(ticker)

        return {
            "kline": kline,
            "quote": quote,
            "metadata": metadata,
        }

    # ── 批量获取 ──────────────────────────────────────────────

    def fetch_quotes_batch(
        self,
        tickers: list[str],
        max_workers: int = 8,
    ) -> dict[str, RealtimeQuote]:
        """批量获取实时行情，按 Provider 链顺序尝试，合并结果。

        对每个 Provider 依次调用其批量方法，后续 Provider 只补充前面
        未获取到的 ticker。akshare 会复用全市场快照缓存，其他 Provider
        使用并发单票拉取。

        Parameters
        ----------
        tickers : list[str]
            股票代码列表。
        max_workers : int
            并发线程数，传给各 Provider 的批量方法。

        Returns
        -------
        dict[str, RealtimeQuote]
            成功获取的 ticker → 行情映射。
        """
        results: dict[str, RealtimeQuote] = {}
        remaining = list(tickers)
        for name in self.provider_chain:
            if not remaining:
                break
            provider = self.get_provider(name)
            if provider is None or not provider.supports_quote:
                continue
            if not self._provider_healthy(name, provider):
                continue
            wait_time = self._rate_limiter.acquire(name)
            if wait_time > 0:
                logger.debug(f"⏳ {name} 限流等待 {wait_time:.1f}s: 批量行情")
                time.sleep(wait_time)
            try:
                batch = provider.fetch_quotes_batch(remaining, max_workers=max_workers)
                if batch:
                    results.update(batch)
                    found = set(batch.keys())
                    remaining = [t for t in remaining if t not in found]
                    self._rate_limiter.record_success(name)
                    self._record_provider_success(name)
                    logger.debug(
                        f"✅ 批量行情 {name}: 获取 {len(batch)} 只，剩余 {len(remaining)} 只"
                    )
            except Exception as e:  # noqa: BLE001
                self._record_provider_fail(name)
                logger.warning(f"{name} 批量行情异常: {str(e)[:100]}")
        return results

    def fetch_metadata_batch(
        self,
        tickers: list[str],
        max_workers: int = 8,
    ) -> dict[str, StockMetadata]:
        """批量获取股票元数据，按 Provider 链顺序尝试，合并结果。

        对每个 Provider 依次调用其批量方法，后续 Provider 只补充前面
        未获取到的 ticker。

        Parameters
        ----------
        tickers : list[str]
            股票代码列表。
        max_workers : int
            并发线程数，传给各 Provider 的批量方法。

        Returns
        -------
        dict[str, StockMetadata]
            成功获取的 ticker → 元数据映射。
        """
        results: dict[str, StockMetadata] = {}
        remaining = list(tickers)
        for name in self.provider_chain:
            if not remaining:
                break
            provider = self.get_provider(name)
            if provider is None or not provider.supports_metadata:
                continue
            if not self._provider_healthy(name, provider):
                continue
            wait_time = self._rate_limiter.acquire(name)
            if wait_time > 0:
                logger.debug(f"⏳ {name} 限流等待 {wait_time:.1f}s: 批量元数据")
                time.sleep(wait_time)
            try:
                batch = provider.fetch_metadata_batch(remaining, max_workers=max_workers)
                if batch:
                    results.update(batch)
                    found = set(batch.keys())
                    remaining = [t for t in remaining if t not in found]
                    self._rate_limiter.record_success(name)
                    self._record_provider_success(name)
                    logger.debug(
                        f"✅ 批量元数据 {name}: 获取 {len(batch)} 只，剩余 {len(remaining)} 只"
                    )
            except Exception as e:  # noqa: BLE001
                self._record_provider_fail(name)
                logger.warning(f"{name} 批量元数据异常: {str(e)[:100]}")
        return results

    # ── 工具方法 ────────────────────────────────────────────────────────

    # available_providers / health_check_all 继承自 ProviderInstanceManager

    # ── 自适应优先级：Benchmark（委托 factory_benchmark）─────────────

    def _benchmark_provider(self, name: str, ticker: str) -> _BenchResult:
        """Benchmark 单个 Provider（委托 factory_benchmark）。"""
        from trade_krono_cli.data_providers.factory_benchmark import (
            _bench_factory_provider,
        )

        return _bench_factory_provider(self, name, ticker)

    def _get_cached_ranked_chain(self, ticker_type: str) -> tuple[list[str], float] | None:
        """读取排名缓存（委托 factory_benchmark）。"""
        return get_cached_ranked_chain_factory(
            self._rank_cache, self._rank_lock, self._rank_cache_ttl_sec(), ticker_type
        )

    def _write_ranked_chain(self, ticker_type: str, ranked_chain: list[str]) -> None:
        """写入排名缓存（委托 factory_benchmark）。"""
        write_ranked_chain_factory(
            self._rank_cache, self._rank_lock, ticker_type, ranked_chain
        )

    def bench_all(
        self,
        ticker: str = "sh.600519",
        workers: int | None = None,
    ) -> list[_BenchResult]:
        """对所有可用 Provider 进行延迟 benchmark，按速度排序。

        Parameters
        ----------
        ticker : str
            用于测试的代表性 ticker（默认沪深主板大盘股）
        workers : int | None
            并发 benchmark 线程数，默认 _bench_workers()

        Returns
        -------
        list[_BenchResult]
            按 latency_ms 升序排列（越快越靠前），失败的排在末尾
        """
        return bench_all_providers(self, ticker=ticker, workers=workers)

    def get_ranked_chain_for_ticker(self, ticker: str) -> list[str]:
        """获取并缓存按速度排序的 Provider 链（ticker 类型级缓存，TTL 10 分钟）。

        同一 ticker 类型（sh/sz/bj）共享缓存。
        北交所（bj.）股票 tonghuashun 始终置顶，不受 benchmark 结果影响。
        """
        return get_ranked_chain_for_ticker_factory(self, ticker)

    def invalidate_rank_cache(self, ticker_type: str) -> None:
        """清除指定 ticker 类型的 Provider 排名缓存。

        Parameters
        ----------
        ticker_type : str
            ticker 前缀，如 "sh"、"sz"、"bj"
        """
        invalidate_rank_cache_factory(self, ticker_type)

    # ── 内部工具 ──────────────────────────────────────────────────────

    def reset_cache(self) -> None:
        """清空进程级缓存（用于测试隔离）."""
        with self._cache_lock:
            self._instance_cache.clear()
        if self._circuit is not None:
            self._circuit.reset()

    # ── 向后兼容的类方法（供测试直接调用）─────────────────────────────

    @classmethod
    def clear_registry(cls) -> None:
        """清空 Provider 类注册表（用于测试隔离）."""
        ProviderInstanceManager.clear_registry()


# ═══════════════════════════════════════════════════════
# 模块级单例
# ════════════════════════════════════════════════════════

_factory_instance: DataProviderFactory | None = None
_factory_lock = threading.Lock()


def get_data_factory() -> DataProviderFactory:
    """获取全局 DataProviderFactory 单例。
    自动从 Settings 读取 primary / fallbacks 配置。

    ``AKSHARE_ENABLED`` / ``MOOTDX_ENABLED`` / ``TONGHUASHUN_ENABLED`` 为 false 时，
    对应数据源会从主源与降级链中剔除。
    """
    global _factory_instance
    if _factory_instance is None:
        with _factory_lock:
            if _factory_instance is None:
                s = get_settings()
                primary = getattr(s, "data_provider", "baostock")
                fallback_str = getattr(s, "data_fallback", "")
                if fallback_str:
                    fallbacks = [x.strip() for x in fallback_str.split(",") if x.strip()]
                else:
                    fallbacks = list(DataProviderFactory._default_fallbacks())
                disabled = {
                    name
                    for name, enabled in (
                        ("akshare", getattr(s, "akshare_enabled", True)),
                        ("mootdx", getattr(s, "mootdx_enabled", True)),
                        ("tonghuashun", getattr(s, "tonghuashun_enabled", True)),
                    )
                    if not enabled
                }
                if disabled:
                    fallbacks = [f for f in fallbacks if f not in disabled]
                    if primary in disabled:
                        logger.warning(f"⚠️ 主数据源 {primary} 已被禁用，改用备用源")
                        primary = fallbacks[0] if fallbacks else "baostock"
                    logger.info(f"数据源开关生效，已禁用: {', '.join(sorted(disabled))}")
                _factory_instance = DataProviderFactory(
                    primary=primary,
                    fallbacks=fallbacks,
                )
    return _factory_instance


def reset_data_factory() -> None:
    """重置全局工厂单例（用于测试隔离）."""
    global _factory_instance
    with _factory_lock:
        _factory_instance = None
    DataProviderFactory._instance_cache.clear()
    DataProviderFactory._PROVIDER_REGISTRY.clear()
    DataProviderFactory._rank_cache.clear()
