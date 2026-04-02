from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, List, Literal, Optional

if TYPE_CHECKING:
    from selfix.attempt import AttemptRecord
    from selfix.signals.base import Signal
    from selfix.validator.protocol import ValidationResult


@dataclass
class SelfixResult:
    status: Literal["success", "failed", "escalated"]
    signal: "Signal"
    attempts: int
    diff: Optional[str]
    validation_result: "ValidationResult | None"
    agent_reasoning: str
    branch_name: Optional[str]
    attempt_history: "List[AttemptRecord]" = field(default_factory=list)
    # Phase 3
    pr_url: Optional[str] = None
    pr_number: Optional[int] = None
    error: Optional[str] = None
