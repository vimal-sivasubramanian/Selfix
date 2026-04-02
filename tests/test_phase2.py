"""Phase 2 tests: retry loop, feedback injection, build_check, escalation, validators."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from selfix.attempt import AttemptRecord
from selfix.config import EscalationEvent, SelfixConfig
from selfix.graph.nodes.build_check import build_check_node, route_after_build_check
from selfix.graph.nodes.escalation import escalation_node, _build_escalation_report
from selfix.graph.nodes.retry_decision import retry_decision_node
from selfix.graph.orchestrator import build_graph, route_after_retry
from selfix.graph.state import PipelineState
from selfix.signals.manual import ManualSignal
from selfix.validator.builtin import CompositeValidator, PytestValidator, ShellCommandValidator
from selfix.validator.protocol import FixContext, ValidationResult


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(tmp_path, validator=None, max_attempts=3, build_command=None):
    if validator is None:
        validator = AsyncMock()
        validator.validate = AsyncMock(
            return_value=ValidationResult(passed=True, score=1.0, feedback="ok")
        )
    signal = ManualSignal(description="test signal", scope_hint=None)
    return SelfixConfig(
        repo_path=str(tmp_path),
        signal=signal,
        validator=validator,
        max_attempts=max_attempts,
        build_command=build_command,
    )


def _make_vr(passed: bool, feedback: str = "") -> ValidationResult:
    return ValidationResult(passed=passed, score=1.0 if passed else 0.0, feedback=feedback)


def _make_attempt(n: int, passed: bool = True) -> AttemptRecord:
    vr = _make_vr(passed, f"feedback from attempt {n}")
    return AttemptRecord(
        attempt_number=n,
        diff=f"diff {n}",
        agent_reasoning=f"reasoning {n}",
        build_passed=True,
        validation_result=vr,
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
    )


def _make_context(signal=None) -> FixContext:
    if signal is None:
        signal = ManualSignal(description="test signal")
    return FixContext(
        signal=signal,
        repo_path="/tmp",
        diff="",
        attempt_number=1,
        agent_reasoning="",
        previous_feedback=None,
    )


# ---------------------------------------------------------------------------
# route_after_retry
# ---------------------------------------------------------------------------

def test_route_after_retry_success_goes_to_pr_creation():
    state: PipelineState = {"status": "success"}
    assert route_after_retry(state) == "pr_creation"


def test_route_after_retry_escalated_goes_to_escalation():
    state: PipelineState = {"status": "escalated"}
    assert route_after_retry(state) == "escalation"


def test_route_after_retry_running_goes_to_fix_generation():
    state: PipelineState = {"status": "running"}
    assert route_after_retry(state) == "fix_generation"


# ---------------------------------------------------------------------------
# retry_decision_node — success path
# ---------------------------------------------------------------------------

def test_retry_decision_success_on_first_attempt(tmp_path):
    config = _make_config(tmp_path, max_attempts=3)
    vr = _make_vr(passed=True)
    state: PipelineState = {
        "config": config,
        "repo_path": str(tmp_path),
        "attempt_number": 1,
        "validation_result": vr,
        "attempt_history": [],
        "fix_diff": "some diff",
        "agent_reasoning": "did thing",
        "build_check_output": "skipped",
        "base_commit": "",
    }
    result = retry_decision_node(state)
    assert result["status"] == "success"
    assert len(result["attempt_history"]) == 1
    assert result["attempt_history"][0].attempt_number == 1


# ---------------------------------------------------------------------------
# retry_decision_node — retry path
# ---------------------------------------------------------------------------

def test_retry_decision_retries_when_below_max(tmp_path):
    config = _make_config(tmp_path, max_attempts=3)
    vr = _make_vr(passed=False, feedback="tests still failing")
    state: PipelineState = {
        "config": config,
        "repo_path": str(tmp_path),
        "attempt_number": 1,
        "validation_result": vr,
        "attempt_history": [],
        "fix_diff": "diff",
        "agent_reasoning": "tried X",
        "build_check_output": "skipped",
        "base_commit": "",
    }
    result = retry_decision_node(state)
    assert result["status"] == "running"
    assert result["attempt_number"] == 2
    assert result["current_feedback"] == "tests still failing"
    assert result["fix_diff"] is None
    assert result["validation_result"] is None
    assert len(result["attempt_history"]) == 1


def test_retry_decision_escalates_when_at_max(tmp_path):
    config = _make_config(tmp_path, max_attempts=3)
    vr = _make_vr(passed=False, feedback="still broken")
    history = [_make_attempt(1, passed=False), _make_attempt(2, passed=False)]
    state: PipelineState = {
        "config": config,
        "repo_path": str(tmp_path),
        "attempt_number": 3,
        "validation_result": vr,
        "attempt_history": history,
        "fix_diff": "diff",
        "agent_reasoning": "tried Z",
        "build_check_output": "skipped",
        "base_commit": "",
    }
    result = retry_decision_node(state)
    assert result["status"] == "escalated"
    assert result["current_feedback"] == "still broken"
    assert len(result["attempt_history"]) == 3


# ---------------------------------------------------------------------------
# retry_decision_node — feedback carries through
# ---------------------------------------------------------------------------

def test_retry_decision_feedback_set_on_retry(tmp_path):
    config = _make_config(tmp_path, max_attempts=5)
    vr = _make_vr(passed=False, feedback="assertion error on line 42")
    state: PipelineState = {
        "config": config,
        "repo_path": str(tmp_path),
        "attempt_number": 2,
        "validation_result": vr,
        "attempt_history": [_make_attempt(1, passed=False)],
        "fix_diff": "diff",
        "agent_reasoning": "tried again",
        "build_check_output": "skipped",
        "base_commit": "",
    }
    result = retry_decision_node(state)
    assert result["current_feedback"] == "assertion error on line 42"


# ---------------------------------------------------------------------------
# build_check_node
# ---------------------------------------------------------------------------

def test_build_check_skipped_when_no_command(tmp_path):
    config = _make_config(tmp_path, build_command=None)
    state: PipelineState = {"config": config, "repo_path": str(tmp_path)}
    result = build_check_node(state)
    assert result["build_check_output"] == "skipped"
    assert "validation_result" not in result


def test_build_check_passes_on_exit_0(tmp_path):
    config = _make_config(tmp_path, build_command="exit 0")
    state: PipelineState = {"config": config, "repo_path": str(tmp_path)}
    result = build_check_node(state)
    assert result["build_check_output"] is not None
    assert "validation_result" not in result


def test_build_check_fails_on_nonzero_exit(tmp_path):
    config = _make_config(tmp_path, build_command="exit 1")
    state: PipelineState = {"config": config, "repo_path": str(tmp_path)}
    result = build_check_node(state)
    assert result["build_check_output"] is not None
    assert result["validation_result"].passed is False
    assert "Build failed" in result["validation_result"].feedback


# ---------------------------------------------------------------------------
# route_after_build_check
# ---------------------------------------------------------------------------

def test_route_after_build_check_no_result_goes_to_validation():
    state: PipelineState = {}
    assert route_after_build_check(state) == "validation"


def test_route_after_build_check_with_result_skips_to_retry():
    vr = _make_vr(passed=False)
    state: PipelineState = {"validation_result": vr}
    assert route_after_build_check(state) == "retry_decision"


# ---------------------------------------------------------------------------
# escalation_node — report written
# ---------------------------------------------------------------------------

def test_escalation_node_writes_report(tmp_path):
    import subprocess
    subprocess.run(["git", "init"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, check=True)
    # Create an initial commit so HEAD exists
    (tmp_path / "README.md").write_text("init")
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=tmp_path, check=True)

    config = _make_config(tmp_path, max_attempts=3)
    signal = ManualSignal(description="fix the bug")
    history = [_make_attempt(1, passed=False), _make_attempt(2, passed=False)]
    state: PipelineState = {
        "config": config,
        "signal": signal,
        "repo_path": str(tmp_path),
        "branch_name": "selfix/fix-abc",
        "attempt_number": 3,
        "attempt_history": history,
    }
    result = escalation_node(state)
    assert result["status"] == "escalated"
    report_path = tmp_path / ".selfix" / "escalation-report.md"
    assert report_path.exists()
    content = report_path.read_text()
    assert "fix the bug" in content
    assert "Attempt 1" in content
    assert "Attempt 2" in content


def test_build_escalation_report_structure(tmp_path):
    config = _make_config(tmp_path, max_attempts=3)
    signal = ManualSignal(description="fix the O(n^2) sort")
    history = [_make_attempt(1, passed=False)]
    state: PipelineState = {
        "config": config,
        "signal": signal,
        "repo_path": str(tmp_path),
        "branch_name": "selfix/fix-abc",
        "attempt_number": 2,
        "attempt_history": history,
    }
    report = _build_escalation_report(state)
    assert "fix the O(n^2) sort" in report
    assert "Attempt 1" in report
    assert "Manual intervention" in report


# ---------------------------------------------------------------------------
# CompositeValidator
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_composite_all_passes_when_all_pass():
    v1 = ShellCommandValidator("exit 0")
    v2 = ShellCommandValidator("exit 0")
    cv = CompositeValidator([v1, v2], mode="all")
    result = await cv.validate("/tmp", _make_context())
    assert result.passed is True


@pytest.mark.asyncio
async def test_composite_all_fails_if_one_fails():
    v1 = ShellCommandValidator("exit 0")
    v2 = ShellCommandValidator("exit 1")
    cv = CompositeValidator([v1, v2], mode="all")
    result = await cv.validate("/tmp", _make_context())
    assert result.passed is False
    assert "Validator 1: PASSED" in result.feedback
    assert "Validator 2: FAILED" in result.feedback


@pytest.mark.asyncio
async def test_composite_any_passes_if_one_passes():
    v1 = ShellCommandValidator("exit 1")
    v2 = ShellCommandValidator("exit 0")
    cv = CompositeValidator([v1, v2], mode="any")
    result = await cv.validate("/tmp", _make_context())
    assert result.passed is True


@pytest.mark.asyncio
async def test_composite_score_is_average():
    v1 = ShellCommandValidator("exit 0")   # score=1.0
    v2 = ShellCommandValidator("exit 1")   # score=0.0
    cv = CompositeValidator([v1, v2], mode="all")
    result = await cv.validate("/tmp", _make_context())
    assert result.score == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# Graph structure still has all nodes
# ---------------------------------------------------------------------------

def test_build_graph_has_all_nodes(tmp_path):
    graph = build_graph(checkpoint_dir=str(tmp_path / "cp"))
    nodes = set(graph.get_graph().nodes.keys())
    expected = {
        "signal_intake", "repo_setup", "exploration", "fix_generation",
        "build_check", "validation", "retry_decision", "report",
        "escalation", "pr_creation",
    }
    assert expected.issubset(nodes)


# ---------------------------------------------------------------------------
# AttemptRecord
# ---------------------------------------------------------------------------

def test_attempt_record_fields():
    vr = _make_vr(True)
    now = datetime.now(timezone.utc)
    record = AttemptRecord(
        attempt_number=1,
        diff="--- a\n+++ b",
        agent_reasoning="changed foo to bar",
        build_passed=True,
        validation_result=vr,
        started_at=now,
        completed_at=now,
    )
    assert record.attempt_number == 1
    assert record.build_passed is True
    assert record.validation_result is vr
