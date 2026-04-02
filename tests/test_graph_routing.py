"""Tests for LangGraph graph structure and routing logic."""
import pytest

from selfix.config import SelfixConfig
from selfix.graph.nodes.retry_decision import retry_decision_node
from selfix.graph.nodes.signal_intake import signal_intake_node
from selfix.graph.orchestrator import build_graph, route_after_retry
from selfix.graph.state import PipelineState
from selfix.signals.manual import ManualSignal
from selfix.validator.protocol import ValidationResult


def _make_config(tmp_path, validator=None, max_attempts=3):
    from unittest.mock import AsyncMock

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
    )


# ---------------------------------------------------------------------------
# signal_intake_node
# ---------------------------------------------------------------------------

def test_signal_intake_initialises_state(tmp_path):
    config = _make_config(tmp_path)
    state: PipelineState = {"config": config}
    result = signal_intake_node(state)

    assert result["signal"] is config.signal
    assert result["repo_path"] == str(tmp_path)
    assert result["attempt_number"] == 1
    assert result["status"] == "running"
    assert result["error"] is None


# ---------------------------------------------------------------------------
# retry_decision_node (Phase 2 behaviour)
# ---------------------------------------------------------------------------

def test_retry_decision_success_when_passed(tmp_path):
    config = _make_config(tmp_path)
    vr = ValidationResult(passed=True, score=1.0, feedback="all good")
    state: PipelineState = {
        "config": config,
        "repo_path": str(tmp_path),
        "attempt_number": 1,
        "validation_result": vr,
        "attempt_history": [],
        "fix_diff": "diff",
        "agent_reasoning": "did it",
        "build_check_output": "skipped",
        "base_commit": "",
    }
    result = retry_decision_node(state)
    assert result["status"] == "success"


def test_retry_decision_retries_when_not_passed(tmp_path):
    config = _make_config(tmp_path, max_attempts=3)
    vr = ValidationResult(passed=False, score=0.0, feedback="nope")
    state: PipelineState = {
        "config": config,
        "repo_path": str(tmp_path),
        "attempt_number": 1,
        "validation_result": vr,
        "attempt_history": [],
        "fix_diff": "diff",
        "agent_reasoning": "tried",
        "build_check_output": "skipped",
        "base_commit": "",
    }
    result = retry_decision_node(state)
    # Phase 2: retries rather than returning "failed"
    assert result["status"] == "running"
    assert result["attempt_number"] == 2


def test_retry_decision_escalates_when_no_config(tmp_path):
    """Without a config, max_attempts defaults to 3. attempt_number=3 → escalate."""
    config = _make_config(tmp_path, max_attempts=3)
    vr = ValidationResult(passed=False, score=0.0, feedback="nope")
    state: PipelineState = {
        "config": config,
        "repo_path": str(tmp_path),
        "attempt_number": 3,
        "validation_result": vr,
        "attempt_history": [],
        "fix_diff": "diff",
        "agent_reasoning": "tried",
        "build_check_output": "skipped",
        "base_commit": "",
    }
    result = retry_decision_node(state)
    assert result["status"] == "escalated"


# ---------------------------------------------------------------------------
# route_after_retry (Phase 2 routing)
# ---------------------------------------------------------------------------

def test_route_after_retry_success_goes_to_pr_creation():
    state: PipelineState = {"status": "success"}
    assert route_after_retry(state) == "pr_creation"


def test_route_after_retry_running_goes_to_fix_generation():
    state: PipelineState = {"status": "running"}
    assert route_after_retry(state) == "fix_generation"


def test_route_after_retry_escalated_goes_to_escalation():
    state: PipelineState = {"status": "escalated"}
    assert route_after_retry(state) == "escalation"


# ---------------------------------------------------------------------------
# Graph structure
# ---------------------------------------------------------------------------

def test_build_graph_returns_compiled_graph(tmp_path):
    graph = build_graph(checkpoint_dir=str(tmp_path / "cp"))
    assert graph is not None
    nodes = set(graph.get_graph().nodes.keys())
    expected = {
        "signal_intake", "repo_setup", "exploration", "fix_generation",
        "build_check", "validation", "retry_decision", "report",
        "escalation", "pr_creation",
    }
    assert expected.issubset(nodes)
