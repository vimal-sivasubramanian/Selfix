from __future__ import annotations

import logging

from selfix.graph.state import PipelineState

logger = logging.getLogger(__name__)


def escalation_node(state: PipelineState) -> dict:
    """Phase 1: stub. Phase 3 will implement escalation notification."""
    logger.info("escalation: max attempts reached (stub — Phase 3)")
    return {"status": "escalated"}
