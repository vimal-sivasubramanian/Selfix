from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from selfix.signals.base import Signal


@dataclass
class FixContext:
    signal: "Signal"
    repo_path: str
    diff: str
    attempt_number: int
    agent_reasoning: str
    previous_feedback: str | None = None


@dataclass
class ValidationResult:
    passed: bool
    score: float
    feedback: str
    metadata: dict = field(default_factory=dict)


@runtime_checkable
class SelfixValidator(Protocol):
    async def validate(
        self,
        repo_path: str,
        context: FixContext,
    ) -> ValidationResult:
        """
        Validate whether the fix meets the caller's criteria.

        - repo_path: absolute path to the repo (with fix already applied)
        - context: full context about the fix attempt

        Returns ValidationResult. passed=True proceeds to PR/report.
        passed=False with rich feedback enables better retry attempts.
        """
        ...
