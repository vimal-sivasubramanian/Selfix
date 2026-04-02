from __future__ import annotations

from typing import Any, List, Optional

from typing_extensions import TypedDict


class PipelineState(TypedDict, total=False):
    # Inputs — set at pipeline start, never mutated
    config: Any           # SelfixConfig (Any avoids circular-import issues with LangGraph)
    signal: Any           # Signal

    # Repo
    repo_path: str
    branch_name: Optional[str]
    base_commit: str                    # Phase 2: git SHA before any edits

    # Agent outputs
    exploration_summary: Optional[str]
    fix_diff: Optional[str]
    agent_reasoning: Optional[str]

    # Validation
    validation_result: Any              # ValidationResult | None
    attempt_number: int
    build_check_output: Optional[str]   # Phase 2: output from build_check_node

    # Phase 2: retry context
    attempt_history: List[Any]          # list[AttemptRecord]
    current_feedback: Optional[str]     # feedback from last ValidationResult

    # Phase 3: signal enrichment + PR output
    agent_focus_hint: Optional[str]     # signal-type-specific focus hint for the agent
    pr_url: Optional[str]               # PR URL after successful pr_creation
    pr_number: Optional[int]            # PR number after successful pr_creation

    # Pipeline control
    status: str   # "running" | "success" | "failed" | "escalated"
    error: Optional[str]
