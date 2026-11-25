"""trade_krono_cli.kronos_predictor 包 __init__ 单元测试。

覆盖模块级延迟导入入口：通过 __getattr__ 访问 KronosPredictor /
DataPreparator / ResultParser / StreamingPredictor / KronosCacheManager，
以及未知名称触发 AttributeError。
不触发真实模型推理或网络请求。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import ModuleType

from unittest.mock import patch

import pytest


def _import_module() -> ModuleType:
    """导入并返回 kronos_predictor 包模块。"""
    import trade_krono_cli.kronos_predictor as mod

    return mod


class TestKronosPredictorInitLazyImport:
    """kronos_predictor.__init__ 延迟导入入口测试。"""

    def test_kronos_predictor_attribute(self) -> None:
        """访问 KronosPredictor 返回 predictor 模块中的同名类。"""
        mod = _import_module()
        cls = mod.KronosPredictor
        assert cls.__name__ == "KronosPredictor"
        assert cls.__module__.endswith("predictor")

    def test_data_preparator_attribute(self) -> None:
        """访问 DataPreparator 返回 data_prep 模块中的同名类。"""
        mod = _import_module()
        cls = mod.DataPreparator
        assert cls.__name__ == "DataPreparator"
        assert cls.__module__.endswith("data_prep")

    def test_result_parser_attribute(self) -> None:
        """访问 ResultParser 返回 result_parser 模块中的同名类。"""
        mod = _import_module()
        cls = mod.ResultParser
        assert cls.__name__ == "ResultParser"
        assert cls.__module__.endswith("result_parser")

    def test_streaming_predictor_attribute(self) -> None:
        """访问 StreamingPredictor 返回 streaming 模块中的同名类。"""
        mod = _import_module()
        cls = mod.StreamingPredictor
        assert cls.__name__ == "StreamingPredictor"
        assert cls.__module__.endswith("streaming")

    def test_kronos_cache_manager_attribute(self) -> None:
        """访问 KronosCacheManager 返回 cache 模块中的同名类。"""
        mod = _import_module()
        cls = mod.KronosCacheManager
        assert cls.__name__ == "KronosCacheManager"
        assert cls.__module__.endswith("cache")

    def test_unknown_attribute_raises_attribute_error(self) -> None:
        """访问不存在的名称应抛出 AttributeError。"""
        mod = _import_module()
        with pytest.raises(AttributeError, match="has no attribute"):
            _ = mod.NonExistentName  # type: ignore[attr-defined]

    def test_all_exports_are_lazy(self) -> None:
        """__all__ 中列出的所有名称均可通过属性访问获取。"""
        mod = _import_module()
        for name in mod.__all__:
            assert hasattr(mod, name), f"缺少导出名称: {name}"

    def test_getattr_delegation_is_cached(self) -> None:
        """同一名称重复访问返回同一类对象（模块级缓存语义）。"""
        mod = _import_module()
        first = mod.KronosPredictor
        second = mod.KronosPredictor
        assert first is second

    def test_patch_predictor_module_affects_attribute(self) -> None:
        """mock 掉 predictor 模块后，KronosPredictor 属性应反映替换。"""
        mod = _import_module()
        fake_cls = type("FakeKronosPredictor", (), {})
        with patch(
            "trade_krono_cli.kronos_predictor.predictor.KronosPredictor", fake_cls
        ):
            # 重新导入模块以清除已缓存的属性
            import importlib

            importlib.reload(mod)
            assert mod.KronosPredictor is fake_cls


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
