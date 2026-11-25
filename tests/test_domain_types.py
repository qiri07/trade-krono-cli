"""trade_krono_cli.domain.types 单元测试。

覆盖 Signal、Direction、ExperimentType 枚举及 Direction.from_str。
"""

from __future__ import annotations

from trade_krono_cli.domain.types import Direction, ExperimentType, Signal


class TestSignal:
    """Signal 枚举测试。"""

    def test_members(self) -> None:
        assert Signal.BUY == "BUY"
        assert Signal.OVERWEIGHT == "OVERWEIGHT"
        assert Signal.HOLD == "HOLD"
        assert Signal.SELL == "SELL"

    def test_is_str_enum(self) -> None:
        assert isinstance(Signal.BUY, str)
        assert Signal.BUY.value == "BUY"


class TestDirection:
    """Direction 枚举与 from_str 测试。"""

    def test_members(self) -> None:
        assert Direction.UP == "UP"
        assert Direction.DOWN == "DOWN"
        assert Direction.FLAT == "FLAT"

    def test_from_str_valid(self) -> None:
        assert Direction.from_str("up") is Direction.UP
        assert Direction.from_str("UP") is Direction.UP
        assert Direction.from_str("Down") is Direction.DOWN
        assert Direction.from_str("FLAT") is Direction.FLAT

    def test_from_str_none(self) -> None:
        assert Direction.from_str(None) is None

    def test_from_str_invalid(self) -> None:
        assert Direction.from_str("garbage") is None
        assert Direction.from_str("") is None


class TestExperimentType:
    """ExperimentType 枚举测试。"""

    def test_members(self) -> None:
        assert ExperimentType.ALPHA == "alpha"
        assert ExperimentType.MODEL == "model"
        assert ExperimentType.CONFIG == "config"
        assert ExperimentType.DATA == "data"
        assert ExperimentType.HYPOTHESIS == "hypothesis"
