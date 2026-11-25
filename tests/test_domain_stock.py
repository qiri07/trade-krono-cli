"""trade_krono_cli.domain.stock 单元测试。

覆盖 Stock 实体：code 属性、to_dict/from_dict 往返、比较与哈希。
"""

from __future__ import annotations

from trade_krono_cli.domain.stock import Stock


class TestStock:
    def test_defaults(self) -> None:
        s = Stock(ticker="sh.600519")
        assert s.ticker == "sh.600519"
        assert s.name == ""
        assert s.industry == ""
        assert s.market_cap is None
        assert s.pe_ratio is None
        assert s.pb_ratio is None
        assert s.listed_date is None

    def test_code_strips_prefix(self) -> None:
        assert Stock(ticker="sh.600519").code == "600519"
        assert Stock(ticker="sz.000858").code == "000858"
        assert Stock(ticker="bj.920001").code == "920001"
        assert Stock(ticker="600519").code == "600519"

    def test_to_dict_from_dict_roundtrip(self) -> None:
        s = Stock(
            ticker="sh.600519",
            name="贵州茅台",
            industry="白酒",
            market_cap=22600.0,
            pe_ratio=28.5,
            pb_ratio=5.2,
            listed_date="1999-11-10",
        )
        d = s.to_dict()
        assert d == {
            "ticker": "sh.600519",
            "name": "贵州茅台",
            "industry": "白酒",
            "market_cap": 22600.0,
            "pe_ratio": 28.5,
            "pb_ratio": 5.2,
            "listed_date": "1999-11-10",
        }
        s2 = Stock.from_dict(d)
        assert s2 == s
        assert s2.code == "600519"

    def test_from_dict_missing_keys(self) -> None:
        s = Stock.from_dict({"ticker": "sz.000858"})
        assert s.ticker == "sz.000858"
        assert s.name == ""
        assert s.market_cap is None

    def test_comparison_and_hash(self) -> None:
        a = Stock(ticker="sh.600519")
        b = Stock(ticker="sz.000858")
        c = Stock(ticker="sh.600519")
        assert a < b
        assert hash(a) == hash(c)
        assert hash(a) != hash(b)
        assert {a, c} == {a}  # 哈希相同且相等，集合去重
