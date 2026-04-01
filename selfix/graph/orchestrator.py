from __future__ import annotations

import logging

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import StateGraph

from selfix.graph.nodes import (
    build_check_node,
    escalation_node,
    exploration_node,
    fix_generation_node,
    pr_creation_node,
    report_node,
    repo_setup_node,
    retry_decision_node,
    signal_intake_node,
    validation_node,
)
from selfix.graph.state import PipelineState

logger = logging.getLogger(__name__)


def route_after_retry(state: PipelineState) -> str:
    """
    Phase 1: always route to report.
    Phase 2 will implement retry (back to fix_generation) and escalation.
    """
    return "report"


def build_graph():
    builder = StateGraph(PipelineState)

    builder.add_node("signal_intake", signal_intake_node)
    builder.add_node("repo_setup", repo_setup_node)
    builder.add_node("exploration", exploration_node)
    builder.add_node("fix_generation", fix_generation_node)
    builder.add_node("build_check", build_check_node)
    builder.add_node("validation", validation_node)
    builder.add_node("retry_decision", retry_decision_node)
    builder.add_node("report", report_node)
    builder.add_node("escalation", escalation_node)
    builder.add_node("pr_creation", pr_creation_node)

    builder.set_entry_point("signal_intake")

    builder.add_edge("signal_intake", "repo_setup")
    builder.add_edge("repo_setup", "exploration")
    builder.add_edge("exploration", "fix_generation")
    builder.add_edge("fix_generation", "build_check")
    builder.add_edge("build_check", "validation")
    builder.add_edge("validation", "retry_decision")

    builder.add_conditional_edges(
        "retry_decision",
        route_after_retry,
        {
            "fix_generation": "fix_generation",  # Phase 2+
            "pr_creation": "pr_creation",          # Phase 3
            "escalation": "escalation",             # Phase 3
            "report": "report",                     # Phase 1 terminal
        },
    )

    builder.add_edge("pr_creation", "report")
    builder.add_edge("escalation", "report")
    builder.set_finish_point("report")

    checkpointer = MemorySaver()
    return builder.compile(checkpointer=checkpointer)
