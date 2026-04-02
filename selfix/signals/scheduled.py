from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from selfix.signals.base import Signal


@dataclass
class ScheduledSignal(Signal):
    """
    Triggered on a schedule for proactive improvement scans.

    Example — nightly security scan:
        ScheduledSignal(
            description="Nightly security hardening scan",
            cron="0 2 * * *",
            improvement_type="security",
            scope_hint="src/",
        )
    """
    cron: str = ""
    improvement_type: Literal[
        "security", "performance", "maintainability", "coverage", "general"
    ] = "general"
