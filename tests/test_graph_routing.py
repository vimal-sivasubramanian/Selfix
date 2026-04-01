"""Tests for LangGraph graph structure and routing logic."""
import pytest

from selfix.graph.nodes.retry_decision import retry_decision_node
from selfix.graph.nodes.signal_intake import signal_intake_node
from selfix.graph.orchestrator import build_graph, route_after_retry
from selfix.graph.state import PipelineState
from selfix.signals.manual import ManualSignal
from selfix.validator.protocol import ValidationResult


def _make_config(tmp_path, validator=None):
    from unittest.mock import AsyncMock

    from selfix.config import SelfixConfig

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
# retry_decision_node
# ---------------------------------------------------------------------------

def test_retry_decision_success_when_passed():
    vr = ValidationResult(passed=True, score=1.0, feedback="all good")
    state: PipelineState = {"validation_result": vr}
    result = retry_decision_node(state)
    assert result["status"] == "success"


def test_retry_decision_failed_when_not_passed():
    vr = ValidationResult(passed=False, score=0.0, feedback="nope")
    state: PipelineState = {"validation_result": vr}
    result = retry_decision_node(state)
    assert result["status"] == "failed"


def test_retry_decision_failed_when_no_validation():
    state: PipelineState = {}
    result = retry_decision_node(state)
    assert result["status"] == "failed"


# ---------------------------------------------------------------------------
# route_after_retry (Phase 1: always "report")
# ---------------------------------------------------------------------------

def test_route_after_retry_always_report_on_success():
    vr = ValidationResult(passed=True, score=1.0, feedback="ok")
    state: PipelineState = {"status": "success", "validation_result": vr}
    assert route_after_retry(state) == "report"


def test_route_after_retry_always_report_on_failure():
    vr = ValidationResult(passed=False, score=0.0, feedback="fail")
    state: PipelineState = {"status": "failed", "validation_result": vr}
    assert route_after_retry(state) == "report"


# ---------------------------------------------------------------------------
# Graph structure
# ---------------------------------------------------------------------------

def test_build_graph_returns_compiled_graph():
    graph = build_graph()
    # The compiled graph should have a graph attribute with nodes
    assert graph is not None
    nodes = set(graph.get_graph().nodes.keys())
    expected = {
        "signal_intake", "repo_setup", "exploration", "fix_generation",
        "build_check", "validation", "retry_decision", "report",
        "escalation", "pr_creation",
    }
    # All expected nodes should be present (LangGraph may add __start__/__end__)
    assert expected.issubset(nodes)
