from __future__ import annotations

import asyncio
import logging

from selfix.graph.state import PipelineState
from selfix.validator.protocol import FixContext

logger = logging.getLogger(__name__)


def validation_node(state: PipelineState) -> dict:
    context = FixContext(
        signal=state["signal"],
        repo_path=state["repo_path"],
        diff=state.get("fix_diff") or "",
        attempt_number=state.get("attempt_number", 1),
        agent_reasoning=state.get("agent_reasoning") or "",
        previous_feedback=None,  # Phase 2+
    )

    validator = state["config"].validator
    logger.info("Running validator...")
    result = asyncio.get_event_loop().run_until_complete(
        validator.validate(state["repo_path"], context)
    )
    logger.info("Validation: passed=%s score=%.3f", result.passed, result.score)
    return {"validation_result": result}
