from __future__ import annotations

from typing import Any, Literal, Optional

from typing_extensions import TypedDict


class PipelineState(TypedDict, total=False):
    # Inputs — set at pipeline start, never mutated
    config: Any           # SelfixConfig (Any avoids circular-import issues with LangGraph)
    signal: Any           # Signal

    # Repo
    repo_path: str
    branch_name: Optional[str]

    # Agent outputs
    exploration_summary: Optional[str]
    fix_diff: Optional[str]
    agent_reasoning: Optional[str]

    # Validation
    validation_result: Any          # ValidationResult | None
    attempt_number: int

    # Pipeline control
    status: str   # "running" | "success" | "failed" | "escalated"
    error: Optional[str]
