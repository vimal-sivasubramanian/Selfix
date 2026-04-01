from __future__ import annotations

import logging

from selfix.graph.state import PipelineState

logger = logging.getLogger(__name__)


def pr_creation_node(state: PipelineState) -> dict:
    """Phase 1: stub. Phase 3 will implement GitHub/GitLab PR creation."""
    logger.info("pr_creation: stub (Phase 3)")
    return {}
