from __future__ import annotations

import logging

import selfix.git as git_ops
from selfix.agent.prompts import fix_generation_prompt
from selfix.agent.worker import AgentWorker
from selfix.graph.state import PipelineState

logger = logging.getLogger(__name__)


def fix_generation_node(state: PipelineState) -> dict:
    config = state["config"]
    signal = state["signal"]
    repo_path = state["repo_path"]
    exploration_summary = state.get("exploration_summary") or ""

    # Phase 2+: previous_feedback from last ValidationResult
    previous_feedback: str | None = None
    if state.get("validation_result"):
        previous_feedback = state["validation_result"].feedback

    agent_cfg = config.agent_config
    worker = AgentWorker(
        model=agent_cfg.model,
        max_tokens=agent_cfg.max_tokens,
        allowed_tools=["Read", "Edit", "Bash"],
    )

    prompt = fix_generation_prompt(
        signal=signal,
        exploration_summary=exploration_summary,
        repo_path=repo_path,
        previous_feedback=previous_feedback,
    )

    attempt = state.get("attempt_number", 1)
    logger.info("Starting fix generation (attempt %d)...", attempt)
    result = worker.run(prompt)
    logger.info("Fix generation complete (%d tool calls)", result.tool_calls)

    diff = git_ops.get_diff(repo_path)
    logger.info("Diff size: %d bytes", len(diff))

    return {
        "fix_diff": diff,
        "agent_reasoning": result.text,
    }
