"""trading_constraints.st_check 的测试。"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from trade_krono_cli.constraints_config import ConstraintConfig
from trade_krono_cli.trading_constraints.st_check import (
    _is_st_by_name,
    check_st_status,
)


class TestIsStByName:
    """测试 _is_st_by_name。"""

    def test_with_st_name(self) -> None:
        assert _is_st_by_name("sh.600519", "*ST美盛") is True
        assert _is_st_by_name("sh.600519", "ST美盛") is True
        assert _is_st_by_name("sh.600519", "SST美盛") is True
        assert _is_st_by_name("sh.600519", "N ST美盛") is True
        assert _is_st_by_name("sh.600519", "st 美盛") is True  # 不区分大小写

    def test_with_normal_name(self) -> None:
        assert _is_st_by_name("sh.600519", "贵州茅台") is False
        assert _is_st_by_name("sh.600519", "五粮液") is False

    def test_without_name_hint(self) -> None:
        assert _is_st_by_name("sh.600519") is False


class TestCheckStStatus:
    """测试 check_st_status。"""

    def test_config_none(self) -> None:
        result = check_st_status("sh.600519", None)
        assert result is False

    def test_config_st_filter_disabled(self) -> None:
        config = ConstraintConfig(enable_st_filter=False)
        result = check_st_status("sh.600519", config)
        assert result is False

    def test_check_st_status_provider_import_error(self) -> None:
        """当 BaostockProvider 导入失败时，应捕获异常返回 False。"""
        config = ConstraintConfig(enable_st_filter=True)
        with patch(
            "trade_krono_cli.data_providers.baostock_provider.BaostockProvider",
            side_effect=ImportError("no module"),
        ):
            result = check_st_status("sh.600519", config)
            assert result is False

    def test_check_st_status_provider_runtime_error(self) -> None:
        """当 BaostockProvider 实例化抛出 RuntimeError 时，应捕获异常返回 False。"""
        config = ConstraintConfig(enable_st_filter=True)
        with patch(
            "trade_krono_cli.data_providers.baostock_provider.BaostockProvider",
            side_effect=RuntimeError("login failed"),
        ):
            result = check_st_status("sh.600519", config)
            assert result is False

    def test_check_st_status_provider_generic_exception(self) -> None:
        """当 BaostockProvider 抛出普通异常时，应捕获异常返回 False。"""
        config = ConstraintConfig(enable_st_filter=True)
        with patch(
            "trade_krono_cli.data_providers.baostock_provider.BaostockProvider",
            side_effect=Exception("unexpected"),
        ):
            result = check_st_status("sh.600519", config)
            assert result is False

    @patch(
        "trade_krono_cli.data_providers.baostock_provider.BaostockProvider"
    )
    def test_check_st_status_provider_returns_true(
        self, mock_provider_class: MagicMock
    ) -> None:
        mock_provider = MagicMock()
        mock_provider.check_st_status.return_value = True
        mock_provider_class.return_value = mock_provider

        config = ConstraintConfig(enable_st_filter=True)
        result = check_st_status("sh.600519", config)
        assert result is True

    @patch(
        "trade_krono_cli.data_providers.baostock_provider.BaostockProvider"
    )
    def test_check_st_status_provider_returns_false(
        self, mock_provider_class: MagicMock
    ) -> None:
        mock_provider = MagicMock()
        mock_provider.check_st_status.return_value = False
        mock_provider_class.return_value = mock_provider

        config = ConstraintConfig(enable_st_filter=True)
        result = check_st_status("sh.600519", config)
        assert result is False
