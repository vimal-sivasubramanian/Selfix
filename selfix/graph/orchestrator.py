from __future__ import annotations

import logging
import os
import sqlite3

from langgraph.checkpoint.sqlite import SqliteSaver
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
from selfix.graph.nodes.build_check import route_after_build_check
from selfix.graph.state import PipelineState

logger = logging.getLogger(__name__)


def route_after_retry(state: PipelineState) -> str:
    return {
        "success":   "pr_creation",
        "escalated": "escalation",
        "running":   "fix_generation",
    }.get(state.get("status", "running"), "fix_generation")


def build_graph(checkpoint_dir: str = ".selfix/checkpoints"):
    os.makedirs(checkpoint_dir, exist_ok=True)
    db_path = os.path.join(checkpoint_dir, "selfix.db")

    builder = StateGraph(PipelineState)

    builder.add_node("signal_intake",  signal_intake_node)
    builder.add_node("repo_setup",     repo_setup_node)
    builder.add_node("exploration",    exploration_node)
    builder.add_node("fix_generation", fix_generation_node)
    builder.add_node("build_check",    build_check_node)
    builder.add_node("validation",     validation_node)
    builder.add_node("retry_decision", retry_decision_node)
    builder.add_node("report",         report_node)
    builder.add_node("escalation",     escalation_node)
    builder.add_node("pr_creation",    pr_creation_node)

    builder.set_entry_point("signal_intake")

    builder.add_edge("signal_intake",  "repo_setup")
    builder.add_edge("repo_setup",     "exploration")
    builder.add_edge("exploration",    "fix_generation")
    builder.add_edge("fix_generation", "build_check")

    # build_check either short-circuits to retry_decision (on build fail) or proceeds to validation
    builder.add_conditional_edges(
        "build_check",
        route_after_build_check,
        {
            "validation":     "validation",
            "retry_decision": "retry_decision",
        },
    )

    builder.add_edge("validation", "retry_decision")

    builder.add_conditional_edges(
        "retry_decision",
        route_after_retry,
        {
            "fix_generation": "fix_generation",
            "pr_creation":    "pr_creation",
            "escalation":     "escalation",
        },
    )

    builder.add_edge("pr_creation", "report")
    builder.add_edge("escalation",  "report")
    builder.set_finish_point("report")

    conn = sqlite3.connect(db_path, check_same_thread=False)
    checkpointer = SqliteSaver(conn)
    return builder.compile(checkpointer=checkpointer)
