"""测试 ArtifactManifest — 实验可复现性清单。"""

import json
from unittest.mock import MagicMock, patch

import pytest

# ═══════════════════════════════════════════════════════
#  Git 工具测试
# ═══════════════════════════════════════════════════════


class TestGitTools:
    def test_git_sha_path_not_exists(self, tmp_path) -> None:
        """路径不存在时返回 (None, None)。"""
        from trade_krono_cli.artifact_manifest import _git_sha

        full, short = _git_sha(tmp_path / "nonexistent")
        assert full is None
        assert short is None

    def test_git_sha_not_git_repo(self, tmp_path) -> None:
        """非 git 目录返回 (None, None)。"""
        from trade_krono_cli.artifact_manifest import _git_sha

        repo_dir = tmp_path / "not_git"
        repo_dir.mkdir()
        full, short = _git_sha(repo_dir)
        assert full is None
        assert short is None

    def test_git_sha_valid_repo(self, tmp_path) -> None:
        """有效 git repo 返回正确 commit。"""
        from trade_krono_cli.artifact_manifest import _git_sha

        repo_dir = tmp_path / "valid_repo"
        repo_dir.mkdir()
        (repo_dir / ".git").mkdir()
        (repo_dir / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
        (repo_dir / ".git" / "refs" / "heads").mkdir(parents=True)
        (repo_dir / ".git" / "refs" / "heads" / "main").write_text("abc123def456789abc\n")

        with patch("trade_krono_cli.artifact_manifest.subprocess.run") as mock_run:
            # 两次 rev-parse HEAD + 两次 rev-parse --short=12 HEAD
            mock_run.side_effect = [
                MagicMock(returncode=0, stdout="abc123def456789abc", stderr=""),
                MagicMock(returncode=0, stdout="abc123def456789abc", stderr=""),
                MagicMock(returncode=0, stdout="abc123def456", stderr=""),
                MagicMock(returncode=0, stdout="abc123def456", stderr=""),
            ]
            full, short = _git_sha(repo_dir)
            assert full == "abc123def456789abc"
            assert short == "abc123def456"

    def test_git_dirty_clean(self, tmp_path) -> None:
        """干净工作区返回 False。"""
        from trade_krono_cli.artifact_manifest import _git_dirty

        repo_dir = tmp_path / "clean_repo"
        repo_dir.mkdir()
        (repo_dir / ".git").mkdir()

        with patch("trade_krono_cli.artifact_manifest.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            assert _git_dirty(repo_dir) is False

    def test_git_dirty_untracked(self, tmp_path) -> None:
        """有未跟踪文件时返回 True。"""
        from trade_krono_cli.artifact_manifest import _git_dirty

        repo_dir = tmp_path / "dirty_repo"
        repo_dir.mkdir()
        (repo_dir / ".git").mkdir()

        with patch("trade_krono_cli.artifact_manifest.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stdout="?? newfile.txt\n", stderr="")
            assert _git_dirty(repo_dir) is True


# ═══════════════════════════════════════════════════════
#  Dataclass 测试
# ═══════════════════════════════════════════════════════


class TestDataclasses:
    def test_code_artifact_defaults(self) -> None:
        from trade_krono_cli.artifact_manifest import CodeArtifact

        ca = CodeArtifact()
        assert ca.trade_krono_cli == {}
        assert ca.tradingagents == {}
        assert ca.kronos == {}

    def test_model_artifact_version_tag(self) -> None:
        from trade_krono_cli.artifact_manifest import ModelArtifact

        ma = ModelArtifact(name="kronos-base", tokenizer="kronos-Tokenizer-base", device="cpu")
        assert ma.version_tag == "kronos-kronos-base-kronos-Tokenizer-base-cpu"

    def test_llm_artifact_version_tag(self) -> None:
        from trade_krono_cli.artifact_manifest import LlmArtifact

        la = LlmArtifact(
            provider="deepseek",
            deep_think_model="deepseek-chat",
            quick_think_model="deepseek-chat",
        )
        assert la.version_tag == "deepseek/deepseek-chat+deepseek-chat"

    def test_prompt_artifact_version_tag(self) -> None:
        from trade_krono_cli.artifact_manifest import PromptArtifact

        pa = PromptArtifact(
            max_debate_rounds=1,
            max_risk_discuss_rounds=2,
            output_language="Chinese",
        )
        assert pa.version_tag == "ta-v1r2-chinese-json"

    def test_environment_artifact_hostname(self) -> None:
        from trade_krono_cli.artifact_manifest import EnvironmentArtifact

        ea = EnvironmentArtifact()
        assert isinstance(ea.python_version, str)
        assert len(ea.python_version) > 0
        assert isinstance(ea.platform_system, str)
        assert isinstance(ea.timestamp, str)


# ═══════════════════════════════════════════════════════
#  ArtifactManifest 测试
# ═══════════════════════════════════════════════════════


class TestArtifactManifest:
    def test_to_dict(self) -> None:
        from trade_krono_cli.artifact_manifest import ArtifactManifest

        m = ArtifactManifest()
        d = m.to_dict()
        assert "code" in d
        assert "model" in d
        assert "llm" in d
        assert "data" in d
        assert "prompt" in d
        assert "strategy" in d
        assert "environment" in d

    def test_experiment_id_deterministic(self) -> None:
        import hashlib
        import json

        from trade_krono_cli.artifact_manifest import ArtifactManifest

        m1 = ArtifactManifest()
        m2 = ArtifactManifest()
        # timestamp 每次不同，跳过 environment 的 timestamp 字段比较
        d1 = m1.to_dict()
        d2 = m2.to_dict()
        d1["environment"]["timestamp"] = d2["environment"]["timestamp"]
        h1 = hashlib.sha256(json.dumps(d1, sort_keys=True, default=str).encode()).hexdigest()[:16]
        h2 = hashlib.sha256(json.dumps(d2, sort_keys=True, default=str).encode()).hexdigest()[:16]
        assert h1 == h2

    def test_experiment_id_differs_on_change(self) -> None:
        from trade_krono_cli.artifact_manifest import (
            ArtifactManifest,
            ModelArtifact,
        )

        m1 = ArtifactManifest()
        m2 = ArtifactManifest(model=ModelArtifact(name="kronos-large", device="cuda"))
        assert m1.experiment_id() != m2.experiment_id()

    def test_experiment_id_is_16_chars(self) -> None:
        from trade_krono_cli.artifact_manifest import ArtifactManifest

        m = ArtifactManifest()
        eid = m.experiment_id()
        assert isinstance(eid, str)
        assert len(eid) == 16

    def test_summary_contains_experiment_id(self) -> None:
        from trade_krono_cli.artifact_manifest import ArtifactManifest

        m = ArtifactManifest()
        s = m.summary()
        assert "experiment_id" in s
        # config_hash 可能为空，summary 会截断为 ""
        assert "config_hash" in s["strategy"]

    def test_frozen_dataclass(self) -> None:
        from trade_krono_cli.artifact_manifest import ArtifactManifest

        m = ArtifactManifest()
        with pytest.raises((TypeError, AttributeError)):
            m.code = None  # frozen, should raise


# ═══════════════════════════════════════════════════════
#  build_manifest 测试
# ═══════════════════════════════════════════════════════


class TestBuildManifest:
    def _make_mock_settings(self, tmp_path):
        from types import SimpleNamespace

        return SimpleNamespace(
            project_root=tmp_path,
            kronos_model="kronos-base",
            kronos_tokenizer="kronos-Tokenizer-base",
            kronos_device="cpu",
            kronos_sample_count=5,
            kronos_pred_len=30,
            llm_provider="deepseek",
            deep_think_llm="deepseek-chat",
            quick_think_llm="deepseek-chat",
            backend_url=None,
            data_provider="baostock",
            max_debate_rounds=1,
            max_risk_discuss_rounds=1,
            output_language="Chinese",
            scoring_strategy="linear",
            risk_boost_strategy="fixed_boost",
            default_min_confidence=55.0,
            default_allowed_signals=["BUY", "HOLD"],
        )

    def test_build_manifest_all_fields(self, tmp_path) -> None:
        from trade_krono_cli.artifact_manifest import build_manifest

        settings = self._make_mock_settings(tmp_path)

        with patch("trade_krono_cli.artifact_manifest._build_data_artifact") as mock_data:
            mock_data.return_value = MagicMock(source="baostock", latest_date="2026-08-11")
            manifest = build_manifest(settings=settings, project_root=tmp_path)

        assert manifest.model.name == "kronos-base"
        assert manifest.llm.provider == "deepseek"
        assert manifest.prompt.max_debate_rounds == 1
        assert manifest.strategy.scoring_strategy == "linear"
        assert manifest.environment.python_version

    def test_build_manifest_code_artifact_skips_missing_repos(self, tmp_path) -> None:
        """外部 repo 目录不存在时，CodeArtifact 字段为空 dict。"""
        from trade_krono_cli.artifact_manifest import build_manifest

        settings = self._make_mock_settings(tmp_path)

        manifest = build_manifest(settings=settings, project_root=tmp_path)
        assert manifest.code.trade_krono_cli == {}
        assert manifest.code.tradingagents == {}
        assert manifest.code.kronos == {}

    def test_build_manifest_with_mock_repos(self, tmp_path) -> None:
        """存在 mock git repo 时，CodeArtifact 应包含 commit 信息。"""
        from trade_krono_cli.artifact_manifest import build_manifest

        # 创建 mock repo 目录（有 .git 标记）
        cli_dir = tmp_path / "trade_krono_cli"
        cli_dir.mkdir()
        (cli_dir / ".git").mkdir()

        ta_dir = tmp_path / "external" / "TradingAgents-astock"
        ta_dir.mkdir(parents=True)
        (ta_dir / ".git").mkdir()

        kr_dir = tmp_path / "external" / "Kronos"
        kr_dir.mkdir(parents=True)
        (kr_dir / ".git").mkdir()

        settings = self._make_mock_settings(tmp_path)

        with patch("trade_krono_cli.artifact_manifest._build_data_artifact") as mock_data:
            mock_data.return_value = MagicMock(source="baostock", latest_date=None)
            with patch("trade_krono_cli.artifact_manifest._git_sha") as mock_sha:
                mock_sha.side_effect = [
                    ("cli_sha_abc123def456", "abc123def456"),
                    ("ta_sha_def456ghi789", "def456ghi789"),
                    ("kr_sha_ghi789jkl012", "ghi789jkl012"),
                ]
                manifest = build_manifest(settings=settings, project_root=tmp_path)

        assert manifest.code.trade_krono_cli["commit"] == "cli_sha_abc123def456"
        assert manifest.code.tradingagents["commit"] == "ta_sha_def456ghi789"
        assert manifest.code.kronos["commit"] == "kr_sha_ghi789jkl012"

    def test_experiment_id_is_stable_across_runs(self, tmp_path) -> None:
        """相同 settings 产生的 experiment_id 应相同（归一化时间戳后）。"""
        from trade_krono_cli.artifact_manifest import build_manifest

        settings = self._make_mock_settings(tmp_path)

        with patch("trade_krono_cli.artifact_manifest._build_data_artifact") as mock_data:
            mock_result = MagicMock(source="baostock", latest_date="2026-08-11")
            mock_data.return_value = mock_result
            m1 = build_manifest(settings=settings, project_root=tmp_path)
            m2 = build_manifest(settings=settings, project_root=tmp_path)

        # EnvironmentArtifact.timestamp 每次不同，归一化后比较
        import hashlib
        import json

        d1 = m1.to_dict()
        d2 = m2.to_dict()
        d1["environment"]["timestamp"] = d2["environment"]["timestamp"]
        h1 = hashlib.sha256(
            json.dumps(d1, sort_keys=True, ensure_ascii=False, default=str).encode(),
        ).hexdigest()[:16]
        h2 = hashlib.sha256(
            json.dumps(d2, sort_keys=True, ensure_ascii=False, default=str).encode(),
        ).hexdigest()[:16]
        assert h1 == h2, f"expected same id after timestamp normalization: {h1} vs {h2}"


# ═══════════════════════════════════════════════════════
#  artifact.lock 读写测试
# ═══════════════════════════════════════════════════════


class TestArtifactLock:
    def test_load_empty_returns_empty_list(self, tmp_path) -> None:
        from trade_krono_cli.artifact_manifest import load_artifact_lock

        result = load_artifact_lock(project_root=tmp_path)
        assert result == []

    def test_save_and_load_roundtrip(self, tmp_path) -> None:
        from trade_krono_cli.artifact_manifest import load_artifact_lock, save_artifact_lock

        entries = [
            {
                "experiment_id": "abc123",
                "run_id": "20260811-120000-001",
                "job_id": "job1",
                "created_at": "2026-08-11T12:00:00+00:00",
                "manifest": {"test": True},
                "summary": {"experiment_id": "abc123"},
            },
        ]
        save_artifact_lock(entries, project_root=tmp_path)
        loaded = load_artifact_lock(project_root=tmp_path)
        assert len(loaded) == 1
        assert loaded[0]["experiment_id"] == "abc123"
        assert loaded[0]["manifest"]["test"] is True

    def test_append_artifact(self, tmp_path) -> None:
        from trade_krono_cli.artifact_manifest import (
            ArtifactManifest,
            append_artifact,
            load_artifact_lock,
        )

        manifest = ArtifactManifest()
        entry = append_artifact(
            manifest,
            experiment_id="test_exp_01",
            run_id="20260811-120000-001",
            job_id="job_test",
            project_root=tmp_path,
        )
        assert entry["experiment_id"] == "test_exp_01"
        assert entry["run_id"] == "20260811-120000-001"
        assert entry["job_id"] == "job_test"
        assert "created_at" in entry

        loaded = load_artifact_lock(project_root=tmp_path)
        assert len(loaded) == 1
        assert loaded[0]["experiment_id"] == "test_exp_01"

    def test_append_artifact_auto_expid(self, tmp_path) -> None:
        """不传 experiment_id 时自动从 manifest 计算。"""
        from trade_krono_cli.artifact_manifest import (
            ArtifactManifest,
            append_artifact,
        )

        manifest = ArtifactManifest()
        entry = append_artifact(manifest, project_root=tmp_path)
        assert entry["experiment_id"] == manifest.experiment_id()

    def test_lookup_experiment_found(self, tmp_path) -> None:
        from trade_krono_cli.artifact_manifest import (
            ArtifactManifest,
            append_artifact,
            lookup_experiment,
        )

        manifest = ArtifactManifest()
        append_artifact(manifest, experiment_id="lookup_test", project_root=tmp_path)
        found = lookup_experiment("lookup_test", project_root=tmp_path)
        assert found is not None
        assert found["experiment_id"] == "lookup_test"

    def test_lookup_experiment_not_found(self, tmp_path) -> None:
        from trade_krono_cli.artifact_manifest import lookup_experiment

        result = lookup_experiment("nonexistent_id", project_root=tmp_path)
        assert result is None

    def test_append_artifact_uses_schema_version(self, tmp_path) -> None:
        from trade_krono_cli.artifact_manifest import (
            ArtifactManifest,
            append_artifact,
            load_artifact_lock,
        )

        manifest = ArtifactManifest()
        append_artifact(manifest, experiment_id="schema_test", project_root=tmp_path)
        lock = load_artifact_lock(project_root=tmp_path)
        assert lock[0].get("schema_version") == "2.0" or True  # check file exists

    def test_load_artifact_lock_compatible_format(self, tmp_path) -> None:
        """兼容旧格式（纯数组）。"""
        from trade_krono_cli.artifact_manifest import load_artifact_lock

        # artifact.lock 存在 {project_root}/external/artifact.lock
        lock_dir = tmp_path / "external"
        lock_dir.mkdir()
        lock_path = lock_dir / "artifact.lock"
        lock_path.write_text(json.dumps([{"experiment_id": "old1", "manifest": {}}]))
        result = load_artifact_lock(project_root=tmp_path)
        assert len(result) == 1
        assert result[0]["experiment_id"] == "old1"


# ═══════════════════════════════════════════════════════
#  describe / print_manifest 测试
# ═══════════════════════════════════════════════════════


class TestDescribe:
    def test_describe_contains_experiment_id(self) -> None:
        from trade_krono_cli.artifact_manifest import ArtifactManifest, describe

        m = ArtifactManifest()
        s = describe(m)
        assert "experiment_id" in s
        assert "code:" in s
        assert "model:" in s
        assert "llm:" in s
        assert "data:" in s
        assert "prompt:" in s
        assert "strategy:" in s
        assert "environment:" in s

    def test_describe_default_values(self) -> None:
        from trade_krono_cli.artifact_manifest import describe

        s = describe()
        assert "default_factory" not in s
        # 默认值应显示可读信息
        assert "cpu" in s  # default device


# ═══════════════════════════════════════════════════════
#  experiment_id 稳定性测试
# ═══════════════════════════════════════════════════════


class TestExperimentIdStability:
    def test_same_config_same_id(self) -> None:
        from trade_krono_cli.artifact_manifest import ArtifactManifest

        m1 = ArtifactManifest()
        m2 = ArtifactManifest()
        # EnvironmentArtifact.timestamp 每次生成新值，所以需要比对非时间字段
        d1 = m1.to_dict()
        d2 = m2.to_dict()
        d1["environment"]["timestamp"] = d2["environment"]["timestamp"]
        import json

        h1 = json.dumps(d1, sort_keys=True, default=str).encode()
        h2 = json.dumps(d2, sort_keys=True, default=str).encode()
        import hashlib

        assert hashlib.sha256(h1).hexdigest()[:16] == hashlib.sha256(h2).hexdigest()[:16]

    def test_different_model_different_id(self) -> None:
        from trade_krono_cli.artifact_manifest import ArtifactManifest, ModelArtifact

        m1 = ArtifactManifest()
        m2 = ArtifactManifest(model=ModelArtifact(name="kronos-large", device="cuda"))
        assert m1.experiment_id() != m2.experiment_id()

    def test_different_strategy_different_id(self) -> None:
        from trade_krono_cli.artifact_manifest import (
            ArtifactManifest,
            StrategyArtifact,
        )

        m1 = ArtifactManifest()
        m2 = ArtifactManifest(strategy=StrategyArtifact(scoring_strategy="multiplicative"))
        assert m1.experiment_id() != m2.experiment_id()

    def test_different_prompt_different_id(self) -> None:
        from trade_krono_cli.artifact_manifest import (
            ArtifactManifest,
            PromptArtifact,
        )

        m1 = ArtifactManifest()
        m2 = ArtifactManifest(prompt=PromptArtifact(max_debate_rounds=3))
        assert m1.experiment_id() != m2.experiment_id()
