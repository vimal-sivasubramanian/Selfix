from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from selfix.signals.base import Signal
    from selfix.validator.protocol import ValidationResult


@dataclass
class SelfixResult:
    status: Literal["success", "failed", "escalated"]
    signal: "Signal"
    attempts: int
    diff: str | None
    validation_result: "ValidationResult | None"
    agent_reasoning: str
    branch_name: str | None
    error: str | None = None
