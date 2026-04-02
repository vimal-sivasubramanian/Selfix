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
    attempt_number = state.get("attempt_number", 1)
    max_attempts = config.max_attempts
    attempt_history = list(state.get("attempt_history") or [])
    current_feedback = state.get("current_feedback")

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
        attempt_number=attempt_number,
        max_attempts=max_attempts,
        attempt_history=attempt_history,
        current_feedback=current_feedback,
    )

    logger.info("Starting fix generation (attempt %d/%d)...", attempt_number, max_attempts)
    result = worker.run(prompt)
    logger.info("Fix generation complete (%d tool calls)", result.tool_calls)

    diff = git_ops.get_diff(repo_path)
    logger.info("Diff size: %d bytes", len(diff))

    return {
        "fix_diff": diff,
        "agent_reasoning": result.text,
    }
