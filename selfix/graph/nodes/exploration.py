from __future__ import annotations

import logging

from selfix.agent.prompts import exploration_prompt
from selfix.agent.worker import AgentWorker
from selfix.graph.state import PipelineState

logger = logging.getLogger(__name__)


def exploration_node(state: PipelineState) -> dict:
    config = state["config"]
    signal = state["signal"]
    repo_path = state["repo_path"]

    agent_cfg = config.agent_config
    worker = AgentWorker(
        model=agent_cfg.model,
        max_tokens=agent_cfg.max_tokens,
        allowed_tools=["Read", "Glob", "Grep"],  # read-only during exploration
    )

    prompt = exploration_prompt(signal, repo_path)
    logger.info("Starting exploration agent...")
    result = worker.run(prompt)
    logger.info("Exploration complete (%d tool calls)", result.tool_calls)

    return {"exploration_summary": result.text}
