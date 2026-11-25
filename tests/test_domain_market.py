"""trade_krono_cli.domain.market 单元测试。

覆盖 MarketSnapshot：change_pct、day_range_pct、contains_future、
to_dict/from_dict 往返、sector 回退到 stock.industry。
"""

from __future__ import annotations

import pytest

from trade_krono_cli.domain.market import MarketSnapshot
from trade_krono_cli.domain.stock import Stock


def _make_stock() -> Stock:
    return Stock(ticker="sh.600519", name="贵州茅台", industry="白酒")


class TestMarketSnapshot:
    def test_defaults(self) -> None:
        snap = MarketSnapshot(
            stock=_make_stock(),
            date="2026-08-11",
            close=1800.0,
            open=1790.0,
            high=1810.0,
            low=1785.0,
            volume=1e6,
            prev_close=1795.0,
        )
        assert snap.stock.ticker == "sh.600519"
        assert snap.turnover_rate is None
        assert snap.limit_up_price is None
        assert snap.sector == ""

    def test_change_pct(self) -> None:
        snap = MarketSnapshot(
            stock=_make_stock(), date="2026-08-11", close=110.0, open=100.0,
            high=110.0, low=100.0, volume=1e6, prev_close=100.0,
        )
        assert snap.change_pct == pytest.approx(10.0)

        snap_prev_zero = MarketSnapshot(
            stock=_make_stock(), date="2026-08-11", close=100.0, open=100.0,
            high=100.0, low=100.0, volume=1e6, prev_close=0.0,
        )
        assert snap_prev_zero.change_pct == 0.0

    def test_day_range_pct(self) -> None:
        snap = MarketSnapshot(
            stock=_make_stock(), date="2026-08-11", close=105.0, open=100.0,
            high=110.0, low=90.0, volume=1e6, prev_close=100.0,
        )
        assert snap.day_range_pct == pytest.approx(20.0)

        snap_open_zero = MarketSnapshot(
            stock=_make_stock(), date="2026-08-11", close=100.0, open=0.0,
            high=100.0, low=100.0, volume=1e6, prev_close=100.0,
        )
        assert snap_open_zero.day_range_pct == 0.0

    def test_contains_future(self) -> None:
        snap = MarketSnapshot(
            stock=_make_stock(), date="2026-08-11", close=100.0, open=100.0,
            high=100.0, low=100.0, volume=1e6, prev_close=100.0,
        )
        assert snap.contains_future("2026-08-12") is True
        assert snap.contains_future("2026-08-11") is False
        assert snap.contains_future("2026-08-10") is False

    def test_to_dict_from_dict_roundtrip(self) -> None:
        snap = MarketSnapshot(
            stock=_make_stock(),
            date="2026-08-11",
            close=1800.0,
            open=1790.0,
            high=1810.0,
            low=1785.0,
            volume=1e6,
            prev_close=1795.0,
            turnover_rate=0.5,
            limit_up_price=1890.0,
            limit_down_price=1695.0,
            sector="消费",
            extra={"key": "val"},
        )
        d = snap.to_dict()
        assert d["stock"]["ticker"] == "sh.600519"
        assert d["change_pct"] == pytest.approx(0.2785, rel=1e-3)
        assert d["sector"] == "消费"
        assert d["extra"] == {"key": "val"}

        snap2 = MarketSnapshot.from_dict(d)
        assert snap2.stock.ticker == "sh.600519"
        assert snap2.date == "2026-08-11"
        assert snap2.close == 1800.0
        assert snap2.turnover_rate == 0.5
        assert snap2.limit_up_price == 1890.0
        assert snap2.sector == "消费"
        assert snap2.extra == {"key": "val"}

    def test_sector_falls_back_to_industry(self) -> None:
        snap = MarketSnapshot(
            stock=Stock(ticker="sh.600519", industry="白酒"),
            date="2026-08-11", close=100.0, open=100.0, high=100.0,
            low=100.0, volume=1e6, prev_close=100.0,
        )
        assert snap.to_dict()["sector"] == "白酒"
