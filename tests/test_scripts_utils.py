"""tests for scripts._utils shared utilities."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from scripts._utils import (
    get_all_tickers,
    get_provider_chain,
    load_provider_config,
    normalize_ticker_key,
    reset_providers_config,
    set_providers_config_file,
)


class TestGetProviderChain:
    """Provider chain logic tests."""

    def test_sh_ticker(self) -> None:
        # 沪深普通股锁定 baostock 为 K 线复权唯一源（避免多源基准漂移）
        assert get_provider_chain("sh.600519") == ["baostock", "tonghuashun", "mootdx"]

    def test_sz_ticker(self) -> None:
        assert get_provider_chain("sz.000001") == ["baostock", "tonghuashun", "mootdx"]

    def test_sh_etf_ticker(self) -> None:
        """沪市 ETF（51/58 开头）baostock 优先。"""
        assert get_provider_chain("sh.510300") == ["baostock", "tonghuashun", "mootdx"]
        assert get_provider_chain("sh.588000") == ["baostock", "tonghuashun", "mootdx"]

    def test_sz_etf_ticker(self) -> None:
        """深市 ETF（159/156/158 开头）baostock 优先。"""
        assert get_provider_chain("sz.159915") == ["baostock", "tonghuashun", "mootdx"]
        assert get_provider_chain("sz.156001") == ["baostock", "tonghuashun", "mootdx"]

    def test_bj_ticker(self) -> None:
        assert get_provider_chain("bj.920001") == ["tonghuashun", "baostock"]

    def test_unknown_prefix(self) -> None:
        assert get_provider_chain("unknown.123456") == ["baostock", "tonghuashun"]

    def test_empty_ticker(self) -> None:
        assert get_provider_chain("") == ["baostock", "tonghuashun"]


class TestGetProviderChainWithYaml:
    """provider 链从 configs/providers.yaml 读取的测试。

    测试前用 set_providers_config_file 指向临时文件，teardown 恢复默认路径。
    """

    @pytest.fixture(autouse=True)
    def _restore_config(self) -> None:
        reset_providers_config()
        yield
        reset_providers_config()

    def _write_yaml(self, tmp_path: Path, content: str) -> None:
        p = tmp_path / "providers.yaml"
        p.write_text(content, encoding="utf-8")
        set_providers_config_file(p)

    def test_defaults_from_yaml(self, tmp_path: Path) -> None:
        """YAML defaults 生效，sh/sz 链来自配置而非硬编码。"""
        self._write_yaml(
            tmp_path,
            "defaults:\n"
            "  sh: [tushare, baostock]\n"
            "  sz: [mootdx, baostock]\n"
            "  bj: [baostock]\n"
            "  hk: [akshare_hk]\n",
        )
        assert get_provider_chain("sh.600519") == ["tushare", "baostock"]
        assert get_provider_chain("sz.000001") == ["mootdx", "baostock"]

    def test_etf_section_from_yaml(self, tmp_path: Path) -> None:
        """ETF 走 etf 段，与普通票 defaults 区分。"""
        self._write_yaml(
            tmp_path,
            "defaults:\n"
            "  sh: [baostock]\n"
            "  sz: [baostock]\n"
            "etf:\n"
            "  sh: [tonghuashun, baostock]\n"
            "  sz: [mootdx, baostock]\n",
        )
        assert get_provider_chain("sh.600519") == ["baostock"]
        assert get_provider_chain("sh.510300") == ["tonghuashun", "baostock"]
        assert get_provider_chain("sz.159915") == ["mootdx", "baostock"]

    def test_override_single_ticker(self, tmp_path: Path) -> None:
        """override 精确覆盖单只票，优先级高于 etf/defaults。"""
        self._write_yaml(
            tmp_path,
            "defaults:\n"
            "  sh: [baostock]\n"
            "  sz: [baostock]\n"
            "etf:\n"
            "  sh: [tonghuashun]\n"
            "override:\n"
            "  sh.600519: [tushare, baostock]\n",
        )
        assert get_provider_chain("sh.600519") == ["tushare", "baostock"]
        # 未覆盖的 sh ETF 仍走 etf 段
        assert get_provider_chain("sh.510300") == ["tonghuashun"]
        # 未覆盖的 sh 普通票走 defaults
        assert get_provider_chain("sh.600000") == ["baostock"]

    def test_missing_file_falls_back_to_hardcoded(self, tmp_path: Path) -> None:
        """YAML 文件缺失时回退到内置硬编码链。"""
        set_providers_config_file(tmp_path / "nonexistent.yaml")
        assert get_provider_chain("sh.600519") == ["baostock", "tonghuashun", "mootdx"]
        assert get_provider_chain("bj.920001") == ["tonghuashun", "baostock"]

    def test_corrupt_yaml_falls_back_to_hardcoded(self, tmp_path: Path) -> None:
        """YAML 解析失败（非 dict / 语法错误）时回退到硬编码，不 raise。"""
        p = tmp_path / "providers.yaml"
        p.write_text("defaults: [broken, syntax, ::]", encoding="utf-8")
        set_providers_config_file(p)
        assert get_provider_chain("sh.600519") == ["baostock", "tonghuashun", "mootdx"]

    def test_empty_yaml_fills_defaults(self, tmp_path: Path) -> None:
        """空 YAML 文件：defaults 段缺失，但 sh/sz 仍由 setdefault 补齐。"""
        set_providers_config_file(tmp_path / "missing.yaml")
        cfg = load_provider_config(force_reload=True)
        assert cfg["defaults"]["sh"] == ["baostock", "tonghuashun", "mootdx"]
        assert cfg["fetch"]["timeout_seconds"] == 45
        assert cfg["fetch"]["workers"] == 8


class TestNormalizeTickerKey:
    """normalize_ticker_key 归一化测试。"""

    def test_sh_etf_code(self) -> None:
        assert normalize_ticker_key("510300") == "sh.510300"
        assert normalize_ticker_key("588000") == "sh.588000"

    def test_sz_etf_code(self) -> None:
        assert normalize_ticker_key("159915") == "sz.159915"
        assert normalize_ticker_key("156001") == "sz.156001"

    def test_a_share_code(self) -> None:
        assert normalize_ticker_key("600519") == "sh.600519"
        assert normalize_ticker_key("000858") == "sz.000858"
        assert normalize_ticker_key("920001") == "bj.920001"

    def test_prefixed_passthrough(self) -> None:
        assert normalize_ticker_key("sh.600519") == "sh.600519"
        assert normalize_ticker_key("hk.00700") == "hk.00700"


class TestGetAllTickers:
    """Ticker list retrieval tests."""

    def test_returns_sorted_list(self) -> None:
        with patch("scripts._utils.sqlite3.connect") as mock_connect:
            mock_conn = mock_connect.return_value
            mock_conn.execute.return_value.fetchall.return_value = [
                ("sh.600519",),
                ("sz.000001",),
            ]
            result = get_all_tickers()
            assert isinstance(result, list)
            assert result == sorted(result), "Result must be sorted"

    def test_non_empty(self) -> None:
        with patch("scripts._utils.sqlite3.connect") as mock_connect:
            mock_conn = mock_connect.return_value
            mock_conn.execute.return_value.fetchall.return_value = [
                ("sh.600519",),
                ("sz.000001",),
            ]
            result = get_all_tickers()
            assert len(result) > 0, "Should return at least some tickers"

    def test_all_have_dot(self) -> None:
        with patch("scripts._utils.sqlite3.connect") as mock_connect:
            mock_conn = mock_connect.return_value
            mock_conn.execute.return_value.fetchall.return_value = [
                ("sh.600519",),
                ("sz.000001",),
            ]
            result = get_all_tickers()
            assert all("." in t for t in result), "All tickers should have exchange prefix"

    def test_db_not_found_raises(self) -> None:
        """When DB is missing, should raise an error."""
        with patch("scripts._utils.sqlite3.connect") as mock_connect:
            mock_connect.side_effect = sqlite3.OperationalError("no such file")
            with pytest.raises(sqlite3.OperationalError):
                get_all_tickers()
