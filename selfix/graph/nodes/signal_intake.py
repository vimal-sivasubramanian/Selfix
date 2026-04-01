from __future__ import annotations

import logging

from selfix.graph.state import PipelineState

logger = logging.getLogger(__name__)


def signal_intake_node(state: PipelineState) -> dict:
    signal = state["config"].signal
    logger.info("=" * 60)
    logger.info("Selfix run started")
    logger.info("  Signal ID  : %s", signal.id)
    logger.info("  Description: %s", signal.description[:120])
    logger.info("  Scope hint : %s", signal.scope_hint or "(none)")
    logger.info("=" * 60)

    return {
        "signal": signal,
        "repo_path": state["config"].repo_path,
        "attempt_number": 1,
        "status": "running",
        "error": None,
        "branch_name": None,
        "exploration_summary": None,
        "fix_diff": None,
        "agent_reasoning": None,
        "validation_result": None,
    }
