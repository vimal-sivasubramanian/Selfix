from __future__ import annotations

import logging

import selfix.git as git_ops
from selfix.graph.state import PipelineState
from selfix.result import SelfixResult

logger = logging.getLogger(__name__)

# Stored on module so the orchestrator can retrieve it after graph completes.
_last_result: SelfixResult | None = None


def report_node(state: PipelineState) -> dict:
    global _last_result

    status = state.get("status", "failed")
    vr = state.get("validation_result")
    diff = state.get("fix_diff")
    branch_name = state.get("branch_name")
    repo_path = state.get("repo_path", "")

    # Commit the changes to the fix branch (even on failure — for inspection)
    if diff and repo_path and branch_name:
        msg = f"selfix: {state['signal'].description[:72]}"
        try:
            git_ops.commit_changes(repo_path, msg)
        except Exception as e:
            logger.warning("Could not commit changes: %s", e)

    result = SelfixResult(
        status=status,
        signal=state["signal"],
        attempts=state.get("attempt_number", 1),
        diff=diff,
        validation_result=vr,
        agent_reasoning=state.get("agent_reasoning") or "",
        branch_name=branch_name,
        attempt_history=list(state.get("attempt_history") or []),
        pr_url=state.get("pr_url"),
        pr_number=state.get("pr_number"),
        error=state.get("error"),
    )
    _last_result = result

    logger.info("=" * 60)
    logger.info("Selfix run complete: status=%s", status)
    if vr:
        logger.info("  Validation: passed=%s score=%.3f", vr.passed, vr.score)
    logger.info("  Branch: %s", branch_name)
    logger.info("  Diff size: %d bytes", len(diff or ""))
    logger.info("=" * 60)

    return {}


def get_last_result() -> SelfixResult | None:
    return _last_result
