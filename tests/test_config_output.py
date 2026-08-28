"""测试 OutputConfig 配置类。"""

from __future__ import annotations

from pathlib import Path

from trade_krono_cli.configs.output import OutputConfig


class TestOutputConfig:
    def test_default_values(self):
        cfg = OutputConfig()
        assert cfg.output_dir == Path("outputs")
        assert cfg.json_path == "outputs/results.json"
        assert cfg.html_path == "outputs/report.html"

    def test_custom_paths(self):
        cfg = OutputConfig(
            output_dir=Path("custom_out"),
            json_path="custom_out/result.json",
            html_path="custom_out/report.html",
        )
        assert cfg.output_dir == Path("custom_out")
        assert cfg.json_path == "custom_out/result.json"
        assert cfg.html_path == "custom_out/report.html"

    def test_merge_overrides_json(self):
        cfg = OutputConfig()
        merged = cfg.merge(json_path="outputs/new_results.json")
        assert merged.json_path == "outputs/new_results.json"
        # 其他字段保持不变
        assert merged.output_dir == Path("outputs")
        assert merged.html_path == "outputs/report.html"

    def test_merge_overrides_all(self):
        cfg = OutputConfig(output_dir=Path("data"))
        merged = cfg.merge(
            output_dir=Path("results"),
            json_path="results/out.json",
            html_path="results/out.html",
        )
        assert merged.output_dir == Path("results")
        assert merged.json_path == "results/out.json"
        assert merged.html_path == "results/out.html"

    def test_merge_empty_does_not_change(self):
        cfg = OutputConfig(output_dir=Path("foo"))
        merged = cfg.merge()
        assert merged.output_dir == Path("foo")

    def test_merge_preserves_non_overridden(self):
        cfg = OutputConfig(
            output_dir=Path("a"),
            json_path="a/x.json",
            html_path="a/x.html",
        )
        merged = cfg.merge(json_path="a/y.json")
        assert merged.output_dir == Path("a")
        assert merged.json_path == "a/y.json"
        assert merged.html_path == "a/x.html"
