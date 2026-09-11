"""测试 bs_session — baostock 会话管理模块。"""

from __future__ import annotations

import threading
from unittest.mock import MagicMock, patch

import pytest


class TestGetLimiter:
    """TokenBucket 限流器获取测试。"""

    def test_creates_limiter(self, monkeypatch) -> None:
        from trade_krono_cli.bs_session import _get_limiter

        settings = MagicMock()
        settings.baostock_sleep_sec = 0.5
        limiter = _get_limiter(settings)
        assert limiter is not None

    def test_reuses_existing(self, monkeypatch) -> None:
        from trade_krono_cli.bs_session import _get_limiter, _bs_limiter

        settings = MagicMock()
        settings.baostock_sleep_sec = 1.0
        # 首次创建
        l1 = _get_limiter(settings)
        # 再次调用应返回同一个实例（模块级缓存）
        l2 = _get_limiter(settings)
        assert l1 is l2


class TestClearBaostockGlobals:
    """clear_baostock_globals 重置测试。"""

    def test_resets_all_globals(self, monkeypatch) -> None:
        from trade_krono_cli import bs_session

        # 设置状态
        bs_session._bs = MagicMock()
        bs_session._HAS_BS = True
        bs_session._bs_logged_in = True
        bs_session._bs_limiter = MagicMock()
        bs_session._bs_login_lock = threading.Lock()

        bs_session.clear_baostock_globals()

        assert bs_session._bs is None
        assert bs_session._HAS_BS is False
        assert bs_session._bs_logged_in is False
        assert bs_session._bs_limiter is None

    def test_idempotent(self, monkeypatch) -> None:
        from trade_krono_cli import bs_session

        bs_session.clear_baostock_globals()
        bs_session.clear_baostock_globals()  # 重复调用不应报错


class TestEnsureBsImport:
    """_ensure_bs_import 测试。"""

    def test_success(self, monkeypatch) -> None:
        from trade_krono_cli import bs_session

        mock_bs = MagicMock()
        monkeypatch.setattr(bs_session, "_bs", None)
        monkeypatch.setattr(bs_session, "_HAS_BS", False)

        with patch.dict("sys.modules", {"baostock": mock_bs}):
            bs_session._ensure_bs_import()
            assert bs_session._HAS_BS is True
            assert bs_session._bs is mock_bs

    def test_already_imported(self, monkeypatch) -> None:
        from trade_krono_cli import bs_session

        mock_bs = MagicMock()
        monkeypatch.setattr(bs_session, "_bs", mock_bs)
        monkeypatch.setattr(bs_session, "_HAS_BS", True)

        with patch.dict("sys.modules", {"baostock": mock_bs}):
            bs_session._ensure_bs_import()
            # 应已导入，不再重新导入
            assert bs_session._HAS_BS is True

    def test_import_error(self, monkeypatch) -> None:
        from trade_krono_cli import bs_session

        monkeypatch.setattr(bs_session, "_bs", None)
        monkeypatch.setattr(bs_session, "_HAS_BS", False)

        with patch("builtins.__import__", side_effect=ImportError("no baostock")):
            with pytest.raises(RuntimeError, match="baostock 未安装"):
                bs_session._ensure_bs_import()


class TestEnsureBsLogin:
    """_ensure_bs_login 双重检查锁定测试。"""

    def test_success(self, monkeypatch) -> None:
        from trade_krono_cli import bs_session

        mock_bs = MagicMock()
        mock_login_result = MagicMock()
        mock_login_result.error_code = "0"
        mock_login_result.error_msg = ""
        mock_bs.login.return_value = mock_login_result

        monkeypatch.setattr(bs_session, "_bs", mock_bs)
        monkeypatch.setattr(bs_session, "_HAS_BS", True)
        monkeypatch.setattr(bs_session, "_bs_logged_in", False)

        bs_session._ensure_bs_login()
        assert bs_session._bs_logged_in is True
        mock_bs.login.assert_called_once()

    def test_already_logged_in(self, monkeypatch) -> None:
        from trade_krono_cli import bs_session

        mock_bs = MagicMock()
        monkeypatch.setattr(bs_session, "_bs", mock_bs)
        monkeypatch.setattr(bs_session, "_HAS_BS", True)
        monkeypatch.setattr(bs_session, "_bs_logged_in", True)

        bs_session._ensure_bs_login()
        # 已登录，不应再调用 login
        mock_bs.login.assert_not_called()

    def test_login_failure(self, monkeypatch) -> None:
        from trade_krono_cli import bs_session

        mock_bs = MagicMock()
        mock_login_result = MagicMock()
        mock_login_result.error_code = "10001001"
        mock_login_result.error_msg = "用户未登录"
        mock_bs.login.return_value = mock_login_result

        monkeypatch.setattr(bs_session, "_bs", mock_bs)
        monkeypatch.setattr(bs_session, "_HAS_BS", True)
        monkeypatch.setattr(bs_session, "_bs_logged_in", False)

        with pytest.raises(RuntimeError, match="baostock 登录失败"):
            bs_session._ensure_bs_login()

    def test_concurrent_login(self, monkeypatch) -> None:
        """多线程并发登录：只应调用一次 login。"""
        from trade_krono_cli import bs_session

        mock_bs = MagicMock()
        mock_login_result = MagicMock()
        mock_login_result.error_code = "0"
        mock_login_result.error_msg = ""
        mock_bs.login.return_value = mock_login_result

        monkeypatch.setattr(bs_session, "_bs", mock_bs)
        monkeypatch.setattr(bs_session, "_HAS_BS", True)
        monkeypatch.setattr(bs_session, "_bs_logged_in", False)

        errors: list[Exception] = []

        def worker() -> None:
            try:
                bs_session._ensure_bs_login()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert bs_session._bs_logged_in is True
        # 双重检查锁定确保 login 只被调用一次
        assert mock_bs.login.call_count == 1
