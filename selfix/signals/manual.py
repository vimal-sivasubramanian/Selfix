from __future__ import annotations

from dataclasses import dataclass

from selfix.signals.base import Signal


@dataclass
class ManualSignal(Signal):
    """
    Triggered explicitly by the caller with a plain-text description
    of the problem or improvement to attempt.

    Example:
        ManualSignal(
            description="The Fibonacci function in math/fib.py uses recursion.
                         Convert it to iteration and ensure it handles n > 10000.",
            scope_hint="math/"
        )
    """
    pass
