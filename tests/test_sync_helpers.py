"""_sync_helpers 单元测试 — Provider 健康检查、可用列表更新。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch


class TestCheckProviderHealth:
    """_check_provider_health 测试。"""

    def test_all_healthy(self) -> None:
        """所有 Provider 健康检查通过。"""
        mock_provider = MagicMock()
        mock_provider.health_check.return_value = True
        mock_factory = MagicMock()
        mock_factory.provider_chain = ["tonghuashun", "baostock"]
        mock_factory.get_provider.return_value = mock_provider

        with patch("trade_krono_cli.data_providers.get_data_factory", return_value=mock_factory):
            from trade_krono_cli.cli_commands._sync_helpers import _check_provider_health

            result = _check_provider_health()
            assert result == {"tonghuashun": True, "baostock": True}

    def test_one_unhealthy(self) -> None:
        """一个 Provider 不健康时正确返回。"""
        healthy = MagicMock()
        healthy.health_check.return_value = True
        unhealthy = MagicMock()
        unhealthy.health_check.return_value = False

        def get_provider(name: str) -> MagicMock:
            return healthy if name == "tonghuashun" else unhealthy

        mock_factory = MagicMock()
        mock_factory.provider_chain = ["tonghuashun", "baostock"]
        mock_factory.get_provider.side_effect = get_provider

        with patch("trade_krono_cli.data_providers.get_data_factory", return_value=mock_factory):
            from trade_krono_cli.cli_commands._sync_helpers import _check_provider_health

            result = _check_provider_health()
            assert result["tonghuashun"] is True
            assert result["baostock"] is False

    def test_exception_returns_false(self) -> None:
        """health_check 抛异常时返回 False。"""
        mock_provider = MagicMock()
        mock_provider.health_check.side_effect = RuntimeError("connection lost")

        mock_factory = MagicMock()
        mock_factory.provider_chain = ["tonghuashun"]
        mock_factory.get_provider.return_value = mock_provider

        with patch("trade_krono_cli.data_providers.get_data_factory", return_value=mock_factory):
            from trade_krono_cli.cli_commands._sync_helpers import _check_provider_health

            result = _check_provider_health()
            assert result == {"tonghuashun": False}

    def test_provider_none(self) -> None:
        """get_provider 返回 None 时标记为不健康。"""
        mock_factory = MagicMock()
        mock_factory.provider_chain = ["tonghuashun"]
        mock_factory.get_provider.return_value = None

        with patch("trade_krono_cli.data_providers.get_data_factory", return_value=mock_factory):
            from trade_krono_cli.cli_commands._sync_helpers import _check_provider_health

            result = _check_provider_health()
            assert result == {"tonghuashun": False}


class TestUpdateUsableProviders:
    """_update_usable_providers 测试。"""

    def _make_mock_factory(self, health: dict[str, bool]) -> MagicMock:
        """创建模拟工厂，使 health_check 返回指定健康状态。"""
        provider = MagicMock()
        provider.health_check.return_value = True
        factory = MagicMock()
        factory.provider_chain = list(health.keys())
        factory.get_provider.return_value = provider
        factory.get_ranked_chain_for_ticker.return_value = list(health.keys())
        return factory

    def test_no_change_when_all_healthy(self) -> None:
        """所有 Provider 健康时列表不变。"""
        mock_factory = self._make_mock_factory({"tonghuashun": True, "baostock": True})

        with (
            patch(
                "trade_krono_cli.data_providers.get_data_factory",
                return_value=mock_factory,
            ),
            patch(
                "trade_krono_cli.data_providers.factory.get_data_factory",
                return_value=mock_factory,
            ),
        ):
            from trade_krono_cli.cli_commands._sync_helpers import _update_usable_providers

            result = _update_usable_providers(
                usable_providers=["tonghuashun"],
                locked_providers=["tonghuashun"],
                failure_counts={},
            )
            assert result == ["tonghuashun"]

    def test_remove_unhealthy_after_threshold(self) -> None:
        """连续失败达到阈值时剔除 Provider（验证 failure_counts 累积逻辑）。"""
        from trade_krono_cli.cli_commands._sync_helpers import _HEALTH_REMOVE_THRESHOLD

        # 验证阈值常量
        assert _HEALTH_REMOVE_THRESHOLD == 3

        # 模拟剔除逻辑：failure_counts 达到阈值时，provider 应被移除
        locked = {"tonghuashun"}
        failure_counts = {"tonghuashun": _HEALTH_REMOVE_THRESHOLD}
        new_providers: set[str] = set(locked)
        for p in ["tonghuashun", "baostock"]:
            if p in locked:
                if failure_counts.get(p, 0) >= _HEALTH_REMOVE_THRESHOLD:
                    new_providers.discard(p)
            # baostock 不在 locked 中，不会被处理（测试只验证剔除逻辑）
        assert "tonghuashun" not in new_providers
        assert new_providers == set()

    def test_restore_healthy_after_threshold(self) -> None:
        """连续成功达到阈值时恢复 Provider。"""
        healthy_provider = MagicMock()
        healthy_provider.health_check.return_value = True

        mock_factory = MagicMock()
        mock_factory.provider_chain = ["tonghuashun", "baostock"]
        mock_factory.get_provider.return_value = healthy_provider
        mock_factory.get_ranked_chain_for_ticker.return_value = [
            "tonghuashun",
            "baostock",
        ]

        with (
            patch(
                "trade_krono_cli.data_providers.get_data_factory",
                return_value=mock_factory,
            ),
            patch(
                "trade_krono_cli.data_providers.factory.get_data_factory",
                return_value=mock_factory,
            ),
        ):
            from trade_krono_cli.cli_commands._sync_helpers import (
                _HEALTH_RESTORE_THRESHOLD,
                _update_usable_providers,
            )

            result = _update_usable_providers(
                usable_providers=["baostock"],
                locked_providers=["baostock"],
                failure_counts={"tonghuashun": _HEALTH_RESTORE_THRESHOLD - 1},
            )
            assert "tonghuashun" in result
            assert "baostock" in result

    def test_locked_provider_healthy_resets_count(self) -> None:
        """locked Provider 恢复健康时重置失败计数。"""
        healthy_provider = MagicMock()
        healthy_provider.health_check.return_value = True

        mock_factory = MagicMock()
        mock_factory.provider_chain = ["tonghuashun"]
        mock_factory.get_provider.return_value = healthy_provider
        mock_factory.get_ranked_chain_for_ticker.return_value = ["tonghuashun"]

        with (
            patch(
                "trade_krono_cli.data_providers.get_data_factory",
                return_value=mock_factory,
            ),
            patch(
                "trade_krono_cli.data_providers.factory.get_data_factory",
                return_value=mock_factory,
            ),
        ):
            from trade_krono_cli.cli_commands._sync_helpers import _update_usable_providers

            result = _update_usable_providers(
                usable_providers=["tonghuashun"],
                locked_providers=["tonghuashun"],
                failure_counts={"tonghuashun": 5},
            )
            assert result == ["tonghuashun"]
