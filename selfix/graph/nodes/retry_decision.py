from __future__ import annotations

import logging

from selfix.graph.state import PipelineState

logger = logging.getLogger(__name__)


def retry_decision_node(state: PipelineState) -> dict:
    """
    Phase 1: always routes to report regardless of validation outcome.
    Phase 2 will implement proper retry and escalation logic.
    """
    result = state.get("validation_result")
    status = "success" if (result and result.passed) else "failed"
    logger.info("retry_decision: status=%s", status)
    return {"status": status}
