"""测试 BatchRunner 动态调度（Phase 4）。"""

from trade_krono_cli.batch.batch_runner import BatchConfig, BatchRunner


class TestBatchRunner:
    """BatchRunner 单元测试。"""

    def test_empty_items(self):
        """空列表直接返回。"""
        runner = BatchRunner()
        result = runner.run_sync([], lambda x: x)
        assert result == []

    def test_basic_batch(self):
        """基本批量执行。"""
        runner = BatchRunner(config=BatchConfig(batch_size=2, max_concurrent=2))

        def process(x):
            return x * 2

        items = [1, 2, 3, 4]
        results = runner.run_sync(items, process)
        assert sorted(results) == [2, 4, 6, 8]

    def test_concurrent_execution(self):
        """并发执行：多个任务同时运行。"""
        runner = BatchRunner(config=BatchConfig(max_concurrent=3, cooldown_seconds=0))

        execution_order = []

        def process(x):
            execution_order.append(x)
            return x

        items = list(range(6))
        results = runner.run_sync(items, process)
        assert sorted(results) == items
        # 6 个任务都执行了
        assert len(execution_order) == 6

    def test_error_handling(self):
        """单个任务失败不影响其他任务。"""
        runner = BatchRunner(
            config=BatchConfig(
                batch_size=2,
                max_concurrent=2,
                retry_attempts=0,
            )
        )

        def flaky_process(x):
            if x == 2:
                raise ValueError(f"item {x} failed")
            return x

        items = [1, 2, 3]
        results = runner.run_sync(items, flaky_process)
        # 只有成功的结果返回
        assert sorted(results) == [1, 3]

    def test_retry_on_failure(self):
        """失败后重试，最终成功。"""
        runner = BatchRunner(
            config=BatchConfig(
                batch_size=1,
                max_concurrent=1,
                retry_attempts=2,
                retry_delay=0.01,
            )
        )

        call_count = 0

        def eventually_succeeds(x):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise RuntimeError("not yet")
            return x

        results = runner.run_sync([42], eventually_succeeds)
        assert results == [42]
        assert call_count == 3

    def test_split_batches(self):
        """测试批次分割逻辑。"""
        items = list(range(7))
        batches = BatchRunner._split_batches(items, 3)
        assert len(batches) == 3
        assert batches[0] == [0, 1, 2]
        assert batches[1] == [3, 4, 5]
        assert batches[2] == [6]

    def test_split_batches_exact(self):
        """整除时批次大小均匀。"""
        items = list(range(6))
        batches = BatchRunner._split_batches(items, 3)
        assert len(batches) == 2
        assert all(len(b) == 3 for b in batches)

    def test_split_batches_larger_than_items(self):
        """batch_size 大于 items 数量时只有一批。"""
        items = [1, 2]
        batches = BatchRunner._split_batches(items, 10)
        assert len(batches) == 1
        assert batches[0] == [1, 2]


class TestBatchConfig:
    """BatchConfig 默认值测试。"""

    def test_defaults(self):
        cfg = BatchConfig()
        assert cfg.batch_size == 5
        assert cfg.max_concurrent == 3
        assert cfg.cooldown_seconds == 2.0
        assert cfg.retry_attempts == 2
        assert cfg.retry_delay == 1.0

    def test_custom_values(self):
        cfg = BatchConfig(batch_size=10, max_concurrent=5, cooldown_seconds=0.5)
        assert cfg.batch_size == 10
        assert cfg.max_concurrent == 5
        assert cfg.cooldown_seconds == 0.5
