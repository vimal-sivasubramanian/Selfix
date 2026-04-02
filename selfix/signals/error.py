from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from selfix.signals.base import Signal


@dataclass
class ErrorSignal(Signal):
    """
    Triggered by a detected error: exception, stack trace, or log pattern.

    Example — from Sentry webhook:
        ErrorSignal(
            description="NullPointerException in UserService.getProfile()",
            stack_trace="...",
            file_hint="src/services/UserService.java",
            line_hint=142,
            error_type="NullPointerException",
            frequency=47,
            environment="production",
        )
    """
    stack_trace: Optional[str] = None
    file_hint: Optional[str] = None
    line_hint: Optional[int] = None
    error_type: Optional[str] = None
    frequency: Optional[int] = None
    environment: Optional[str] = None
