"""Phase 3 tests: signal types, router, remote repo, PR creation, webhook."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from selfix.config import SelfixConfig
from selfix.git.pr import PRConfig, PRRequest, PRResult, build_pr_body, build_pr_title
from selfix.git.remote import RepoConfig, RepoManager
from selfix.graph.nodes.signal_intake import signal_intake_node, _build_focus_hint
from selfix.graph.state import PipelineState
from selfix.result import SelfixResult
from selfix.signals.error import ErrorSignal
from selfix.signals.manual import ManualSignal
from selfix.signals.metric import MetricSignal
from selfix.signals.router import SignalRouter
from selfix.signals.scheduled import ScheduledSignal
from selfix.validator.protocol import ValidationResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(tmp_path, repo_path=None, repo_config=None, pr_provider=None):
    signal = ManualSignal(description="test signal")
    validator = AsyncMock()
    validator.validate = AsyncMock(
        return_value=ValidationResult(passed=True, score=1.0, feedback="ok")
    )
    return SelfixConfig(
        repo_path=repo_path or str(tmp_path),
        repo_config=repo_config,
        signal=signal,
        validator=validator,
        pr_provider=pr_provider,
    )


# ---------------------------------------------------------------------------
# Signal types
# ---------------------------------------------------------------------------

class TestErrorSignal:
    def test_fields(self):
        sig = ErrorSignal(
            description="NullPointerException in UserService",
            stack_trace="at UserService.java:42",
            file_hint="src/UserService.java",
            line_hint=42,
            error_type="NullPointerException",
            frequency=10,
            environment="production",
        )
        assert sig.error_type == "NullPointerException"
        assert sig.line_hint == 42
        assert sig.frequency == 10
        assert sig.id  # auto-generated

    def test_focus_hint_full(self):
        sig = ErrorSignal(
            description="NPE",
            file_hint="src/Foo.java",
            line_hint=10,
            error_type="NullPointerException",
            stack_trace="trace",
            frequency=5,
        )
        hint = _build_focus_hint(sig)
        assert "src/Foo.java" in hint
        assert "line 10" in hint
        assert "NullPointerException" in hint
        assert "5 time" in hint

    def test_focus_hint_minimal(self):
        sig = ErrorSignal(description="some error")
        hint = _build_focus_hint(sig)
        assert hint is None


class TestMetricSignal:
    def test_fields(self):
        sig = MetricSignal(
            description="latency regression",
            metric_name="http.latency.p99",
            metric_path="/api/search",
            current_value=340.0,
            baseline_value=80.0,
            threshold=150.0,
            unit="ms",
            direction="lower_is_better",
        )
        assert sig.metric_name == "http.latency.p99"
        assert sig.current_value == 340.0
        assert sig.direction == "lower_is_better"

    def test_focus_hint(self):
        sig = MetricSignal(
            description="latency regression",
            metric_name="http.latency.p99",
            metric_path="/api/search",
            current_value=340.0,
            baseline_value=80.0,
            threshold=150.0,
            unit="ms",
        )
        hint = _build_focus_hint(sig)
        assert "http.latency.p99" in hint
        assert "340.0ms" in hint
        assert "80.0ms" in hint
        assert "lower is better" in hint


class TestScheduledSignal:
    def test_fields(self):
        sig = ScheduledSignal(
            description="nightly security scan",
            cron="0 2 * * *",
            improvement_type="security",
            scope_hint="src/",
        )
        assert sig.cron == "0 2 * * *"
        assert sig.improvement_type == "security"

    def test_focus_hint(self):
        sig = ScheduledSignal(
            description="scan",
            cron="0 2 * * *",
            improvement_type="performance",
            scope_hint="lib/",
        )
        hint = _build_focus_hint(sig)
        assert "performance" in hint
        assert "lib/" in hint


# ---------------------------------------------------------------------------
# signal_intake_node — Phase 3 enrichment
# ---------------------------------------------------------------------------

class TestSignalIntakeNode:
    def _make_state(self, signal, tmp_path) -> PipelineState:
        config = SelfixConfig(
            repo_path=str(tmp_path),
            signal=signal,
            validator=AsyncMock(),
        )
        return {"config": config}

    def test_error_signal_sets_focus_hint(self, tmp_path):
        sig = ErrorSignal(
            description="NPE",
            file_hint="Foo.java",
            line_hint=7,
            error_type="NullPointerException",
        )
        state = self._make_state(sig, tmp_path)
        result = signal_intake_node(state)
        assert result["agent_focus_hint"] is not None
        assert "Foo.java" in result["agent_focus_hint"]

    def test_manual_signal_no_focus_hint(self, tmp_path):
        sig = ManualSignal(description="fix the bug")
        state = self._make_state(sig, tmp_path)
        result = signal_intake_node(state)
        assert result["agent_focus_hint"] is None

    def test_pr_fields_initialized(self, tmp_path):
        sig = ManualSignal(description="x")
        state = self._make_state(sig, tmp_path)
        result = signal_intake_node(state)
        assert result["pr_url"] is None
        assert result["pr_number"] is None

    def test_repo_path_from_repo_config(self, tmp_path):
        sig = ManualSignal(description="x")
        repo_cfg = RepoConfig(url="https://github.com/org/repo", local_path=str(tmp_path))
        config = SelfixConfig(repo_config=repo_cfg, signal=sig, validator=AsyncMock())
        state = {"config": config}
        result = signal_intake_node(state)
        assert result["repo_path"] == str(tmp_path)


# ---------------------------------------------------------------------------
# SignalRouter
# ---------------------------------------------------------------------------

class TestSignalRouter:
    def _make_router(self):
        mock_config = MagicMock()
        config_factory = MagicMock(return_value=mock_config)
        router = SignalRouter(config_factory=config_factory, dedup_window_seconds=300)
        return router, config_factory

    @pytest.mark.asyncio
    async def test_dispatch_calls_selfix_run(self):
        router, _ = self._make_router()
        mock_result = MagicMock()

        with patch("selfix.run", new=AsyncMock(return_value=mock_result)):
            sig = ManualSignal(description="fix something")
            result = await router.dispatch(sig)

        assert result == mock_result

    @pytest.mark.asyncio
    async def test_deduplication_suppresses_second_dispatch(self):
        router, _ = self._make_router()

        with patch("selfix.run", new=AsyncMock(return_value=MagicMock())):
            sig = ErrorSignal(
                description="same error",
                error_type="NullPointerException",
                file_hint="Foo.java",
                line_hint=1,
            )
            first = await router.dispatch(sig)
            second = await router.dispatch(sig)  # duplicate within window

        assert first is not None
        assert second is None

    @pytest.mark.asyncio
    async def test_different_signals_not_deduplicated(self):
        router, _ = self._make_router()

        with patch("selfix.run", new=AsyncMock(return_value=MagicMock())):
            sig1 = ErrorSignal(description="e1", error_type="NPE", file_hint="A.java", line_hint=1)
            sig2 = ErrorSignal(description="e2", error_type="IOE", file_hint="B.java", line_hint=2)
            r1 = await router.dispatch(sig1)
            r2 = await router.dispatch(sig2)

        assert r1 is not None
        assert r2 is not None

    def test_fingerprint_error_signal(self):
        router, _ = self._make_router()
        sig = ErrorSignal(description="x", error_type="NPE", file_hint="A.java", line_hint=1)
        fp = router._fingerprint(sig)
        assert isinstance(fp, str) and len(fp) == 64  # sha256 hex

    def test_fingerprint_metric_signal(self):
        router, _ = self._make_router()
        sig = MetricSignal(description="x", metric_name="latency", metric_path="/api")
        fp = router._fingerprint(sig)
        assert isinstance(fp, str) and len(fp) == 64

    def test_fingerprint_scheduled_signal(self):
        router, _ = self._make_router()
        sig = ScheduledSignal(description="x", cron="0 2 * * *", improvement_type="security")
        fp = router._fingerprint(sig)
        assert isinstance(fp, str) and len(fp) == 64


# ---------------------------------------------------------------------------
# RepoConfig / RepoManager
# ---------------------------------------------------------------------------

class TestRepoConfig:
    def test_defaults(self):
        cfg = RepoConfig(url="https://github.com/org/repo", local_path="/tmp/repo")
        assert cfg.clone_depth == 50
        assert cfg.auth_token is None

    def test_token_injection(self):
        manager = RepoManager()
        url = manager._inject_token("https://github.com/org/repo", "mytoken")
        assert url == "https://mytoken@github.com/org/repo"

    def test_no_token_passthrough(self):
        manager = RepoManager()
        url = manager._inject_token("https://github.com/org/repo", None)
        assert url == "https://github.com/org/repo"


# ---------------------------------------------------------------------------
# PRConfig / PRRequest / PRResult / build helpers
# ---------------------------------------------------------------------------

class TestPRHelpers:
    def _make_state(self, signal, diff=""):
        return {
            "signal": signal,
            "attempt_history": [],
            "validation_result": ValidationResult(passed=True, score=0.95, feedback="All good"),
            "agent_reasoning": "Fixed the null check in UserService",
            "fix_diff": diff,
            "branch_name": "selfix/fix-abc12345-20240101",
            "attempt_number": 1,
        }

    def test_pr_title_error_signal(self):
        sig = ErrorSignal(description="NullPointerException in UserService", error_type="NPE")
        title = build_pr_title(sig)
        assert title.startswith("fix(NPE):")

    def test_pr_title_metric_signal(self):
        sig = MetricSignal(description="latency spike on /api/search", metric_name="http.latency")
        title = build_pr_title(sig)
        assert "http.latency" in title

    def test_pr_title_scheduled_signal(self):
        sig = ScheduledSignal(description="security scan", improvement_type="security")
        title = build_pr_title(sig)
        assert "chore(security)" in title

    def test_pr_title_manual_signal(self):
        sig = ManualSignal(description="fix the auth bug")
        title = build_pr_title(sig)
        assert title.startswith("fix:")

    def test_pr_title_truncated(self):
        sig = ManualSignal(description="x" * 100)
        title = build_pr_title(sig)
        assert len(title) <= 75

    def test_pr_body_contains_key_sections(self):
        sig = ManualSignal(description="fix the search bug")
        state = self._make_state(sig)
        body = build_pr_body(state)
        assert "fix the search bug" in body
        assert "Fixed the null check" in body
        assert "PASSED" in body
        assert "Selfix Autonomous Fix" in body

    def test_pr_body_attempt_history_table(self):
        from selfix.attempt import AttemptRecord

        sig = ManualSignal(description="bug")
        failed_vr = ValidationResult(passed=False, score=0.0, feedback="tests failed")
        passed_vr = ValidationResult(passed=True, score=1.0, feedback="ok")

        rec1 = AttemptRecord(
            attempt_number=1,
            diff="",
            agent_reasoning="tried X",
            build_passed=True,
            validation_result=failed_vr,
            started_at=datetime.utcnow(),
            completed_at=datetime.utcnow(),
        )
        rec2 = AttemptRecord(
            attempt_number=2,
            diff="",
            agent_reasoning="tried Y",
            build_passed=True,
            validation_result=passed_vr,
            started_at=datetime.utcnow(),
            completed_at=datetime.utcnow(),
        )

        state = {
            "signal": sig,
            "attempt_history": [rec1, rec2],
            "validation_result": passed_vr,
            "agent_reasoning": "fixed it",
            "fix_diff": "",
            "branch_name": "selfix/fix-abc",
            "attempt_number": 2,
        }
        body = build_pr_body(state)
        assert "Attempt History" in body
        assert "❌ Failed" in body
        assert "✅ Passed" in body


# ---------------------------------------------------------------------------
# PRConfig defaults
# ---------------------------------------------------------------------------

class TestPRConfig:
    def test_defaults(self):
        cfg = PRConfig()
        assert cfg.base_branch == "main"
        assert "selfix" in cfg.labels
        assert cfg.draft is False
        assert cfg.auto_merge is False


# ---------------------------------------------------------------------------
# SelfixConfig Phase 3 validation
# ---------------------------------------------------------------------------

class TestSelfixConfigPhase3:
    def test_repo_path_and_repo_config_both_none_raises(self):
        with pytest.raises(ValueError, match="repo_path or repo_config"):
            SelfixConfig(signal=ManualSignal(description="x"), validator=AsyncMock())

    def test_repo_path_alone_ok(self, tmp_path):
        cfg = SelfixConfig(
            repo_path=str(tmp_path),
            signal=ManualSignal(description="x"),
            validator=AsyncMock(),
        )
        assert cfg.repo_path == str(tmp_path)

    def test_repo_config_alone_ok(self, tmp_path):
        repo_cfg = RepoConfig(url="https://github.com/org/repo", local_path=str(tmp_path))
        cfg = SelfixConfig(
            repo_config=repo_cfg,
            signal=ManualSignal(description="x"),
            validator=AsyncMock(),
        )
        assert cfg.repo_config is repo_cfg
        assert cfg.repo_path is None

    def test_pr_config_default(self, tmp_path):
        cfg = SelfixConfig(
            repo_path=str(tmp_path),
            signal=ManualSignal(description="x"),
            validator=AsyncMock(),
        )
        assert cfg.pr_config.base_branch == "main"


# ---------------------------------------------------------------------------
# SelfixResult Phase 3 fields
# ---------------------------------------------------------------------------

class TestSelfixResultPhase3:
    def test_pr_fields_present(self):
        sig = ManualSignal(description="x")
        result = SelfixResult(
            status="success",
            signal=sig,
            attempts=1,
            diff=None,
            validation_result=None,
            agent_reasoning="",
            branch_name="selfix/fix-abc",
            pr_url="https://github.com/org/repo/pull/42",
            pr_number=42,
        )
        assert result.pr_url == "https://github.com/org/repo/pull/42"
        assert result.pr_number == 42

    def test_pr_fields_default_none(self):
        sig = ManualSignal(description="x")
        result = SelfixResult(
            status="success",
            signal=sig,
            attempts=1,
            diff=None,
            validation_result=None,
            agent_reasoning="",
            branch_name=None,
        )
        assert result.pr_url is None
        assert result.pr_number is None


# ---------------------------------------------------------------------------
# GitHubPRProvider / GitLabPRProvider — URL parsing
# ---------------------------------------------------------------------------

class TestGitHubPRProvider:
    def test_parse_https_url(self):
        from selfix.git.providers.github import GitHubPRProvider
        p = GitHubPRProvider(token="tok")
        owner, repo = p._parse_repo_url("https://github.com/myorg/myrepo.git")
        assert owner == "myorg"
        assert repo == "myrepo"

    def test_parse_url_without_git_suffix(self):
        from selfix.git.providers.github import GitHubPRProvider
        p = GitHubPRProvider(token="tok")
        owner, repo = p._parse_repo_url("https://github.com/myorg/myrepo")
        assert owner == "myorg"
        assert repo == "myrepo"


class TestGitLabPRProvider:
    def test_parse_project_path(self):
        from selfix.git.providers.gitlab import GitLabPRProvider
        p = GitLabPRProvider(token="tok")
        path = p._parse_project_path("https://gitlab.com/mygroup/myproject.git")
        assert path == "mygroup/myproject"

    def test_parse_nested_path(self):
        from selfix.git.providers.gitlab import GitLabPRProvider
        p = GitLabPRProvider(token="tok")
        path = p._parse_project_path("https://gitlab.example.com/group/sub/project.git")
        assert path == "group/sub/project"
