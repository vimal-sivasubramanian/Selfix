from __future__ import annotations

import logging
from datetime import datetime, timezone

import selfix.git as git_ops
from selfix.attempt import AttemptRecord
from selfix.graph.state import PipelineState

logger = logging.getLogger(__name__)


def retry_decision_node(state: PipelineState) -> dict:
    result = state.get("validation_result")
    attempt = state.get("attempt_number", 1)
    max_attempts = state["config"].max_attempts if "config" in state else 3

    # Build the AttemptRecord for this attempt
    build_output = state.get("build_check_output")
    build_passed = build_output != "skipped" and result is not None and (
        # build passed if validation_result was NOT set by build_check (i.e. build didn't fail)
        # We detect build failure by checking if build_check_output is non-trivial and result.passed is False
        # Simpler: build_passed = True unless build_check set the failed result (no diff/reasoning on build fail)
        bool(state.get("fix_diff") or state.get("agent_reasoning"))
    )

    record = AttemptRecord(
        attempt_number=attempt,
        diff=state.get("fix_diff") or "",
        agent_reasoning=state.get("agent_reasoning") or "",
        build_passed=build_passed,
        validation_result=result,
        started_at=datetime.now(timezone.utc),  # approximate — no per-attempt start time in state
        completed_at=datetime.now(timezone.utc),
    )
    history = list(state.get("attempt_history") or []) + [record]

    if result and result.passed:
        logger.info("retry_decision: PASSED on attempt %d", attempt)
        return {
            "status": "success",
            "attempt_history": history,
        }

    if attempt >= max_attempts:
        logger.info("retry_decision: ESCALATED after %d/%d attempts", attempt, max_attempts)
        return {
            "status": "escalated",
            "attempt_history": history,
            "current_feedback": result.feedback if result else None,
        }

    # Revert repo to base commit before retrying
    base_commit = state.get("base_commit", "")
    if base_commit:
        logger.info("retry_decision: reverting to base commit %s before retry", base_commit[:8])
        git_ops.revert_to_base(state["repo_path"], base_commit)

    logger.info(
        "retry_decision: RETRYING (attempt %d/%d) with feedback", attempt + 1, max_attempts
    )
    return {
        "status": "running",
        "attempt_number": attempt + 1,
        "attempt_history": history,
        "current_feedback": result.feedback if result else None,
        "fix_diff": None,
        "agent_reasoning": None,
        "validation_result": None,
        "build_check_output": None,
    }
