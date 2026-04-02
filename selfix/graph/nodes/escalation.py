from __future__ import annotations

import asyncio
import logging
import os

import selfix.git as git_ops
from selfix.config import EscalationEvent
from selfix.graph.state import PipelineState

logger = logging.getLogger(__name__)


def _build_escalation_report(state: PipelineState) -> str:
    signal = state["signal"]
    attempt_number = state.get("attempt_number", 1)
    max_attempts = state["config"].max_attempts
    branch_name = state.get("branch_name", "unknown")
    history = state.get("attempt_history") or []

    lines = [
        "# Selfix Escalation Report",
        "",
        f"Signal: {signal.description}",
        f"Attempts: {attempt_number} / {max_attempts}",
        f"Branch: {branch_name}",
        "",
    ]

    for record in history:
        lines.append(f"## Attempt {record.attempt_number}")
        lines.append(f"**What was tried:** {record.agent_reasoning[:500] if record.agent_reasoning else '(no reasoning)'}")
        vr = record.validation_result
        if vr:
            lines.append(f"**Validation feedback:** {vr.feedback[:500] if vr.feedback else '(no feedback)'}")
        lines.append("")

    lines += [
        "## Recommendation",
        "Manual intervention required. Review the branch and validation feedback above.",
    ]

    return "\n".join(lines)


def escalation_node(state: PipelineState) -> dict:
    repo_path = state.get("repo_path", "")
    report = _build_escalation_report(state)

    if repo_path:
        report_path = os.path.join(repo_path, ".selfix", "escalation-report.md")
        os.makedirs(os.path.dirname(report_path), exist_ok=True)
        try:
            with open(report_path, "w") as f:
                f.write(report)
            attempt = state.get("attempt_number", 1)
            git_ops.commit_changes(
                repo_path,
                f"selfix: escalation report after {attempt} attempts",
            )
            logger.info("Escalation report written to %s", report_path)
        except Exception as e:
            logger.warning("Could not write escalation report: %s", e)

    handler = state["config"].escalation_handler if "config" in state else None
    if handler:
        event = EscalationEvent(
            signal=state["signal"],
            attempts=list(state.get("attempt_history") or []),
            branch_name=state.get("branch_name"),
        )
        try:
            asyncio.get_event_loop().run_until_complete(handler(event))
        except RuntimeError:
            # No running event loop — schedule via thread if needed; best-effort
            logger.warning("Could not call escalation_handler (no event loop)")

    logger.info("escalation: max attempts reached — branch preserved for inspection")
    return {"status": "escalated"}
