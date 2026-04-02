from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from selfix.validator.protocol import ValidationResult


@dataclass
class AttemptRecord:
    attempt_number: int
    diff: str
    agent_reasoning: str
    build_passed: bool
    validation_result: "ValidationResult"
    started_at: datetime
    completed_at: datetime
