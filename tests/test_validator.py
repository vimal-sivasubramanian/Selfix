"""Tests for validator protocol and ShellCommandValidator."""
import pytest

from selfix.signals.manual import ManualSignal
from selfix.validator.builtin.shell import ShellCommandValidator
from selfix.validator.protocol import FixContext, SelfixValidator, ValidationResult


def make_context(signal=None) -> FixContext:
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
# Protocol structural check
# ---------------------------------------------------------------------------

def test_shell_validator_satisfies_protocol():
    v = ShellCommandValidator("echo ok")
    assert isinstance(v, SelfixValidator)


# ---------------------------------------------------------------------------
# ShellCommandValidator
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_shell_validator_passes_on_exit_0():
    v = ShellCommandValidator("exit 0")
    result = await v.validate("/tmp", make_context())
    assert result.passed is True
    assert result.score == 1.0


@pytest.mark.asyncio
async def test_shell_validator_fails_on_nonzero_exit():
    v = ShellCommandValidator("exit 1")
    result = await v.validate("/tmp", make_context())
    assert result.passed is False
    assert result.score == 0.0


@pytest.mark.asyncio
async def test_shell_validator_captures_output():
    v = ShellCommandValidator("echo 'hello selfix'")
    result = await v.validate("/tmp", make_context())
    assert result.passed is True
    assert "hello selfix" in result.feedback


@pytest.mark.asyncio
async def test_shell_validator_timeout():
    v = ShellCommandValidator("sleep 10", timeout_seconds=1)
    result = await v.validate("/tmp", make_context())
    assert result.passed is False
    assert "timed out" in result.feedback.lower()
    assert result.metadata.get("timed_out") is True


@pytest.mark.asyncio
async def test_shell_validator_metadata_has_exit_code():
    v = ShellCommandValidator("exit 42")
    result = await v.validate("/tmp", make_context())
    assert result.metadata["exit_code"] == 42
    assert result.metadata["command"] == "exit 42"


# ---------------------------------------------------------------------------
# ValidationResult
# ---------------------------------------------------------------------------

def test_validation_result_defaults():
    vr = ValidationResult(passed=True, score=0.9, feedback="great")
    assert vr.metadata == {}


def test_fix_context_defaults():
    ctx = make_context()
    assert ctx.previous_feedback is None
