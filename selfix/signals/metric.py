from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from selfix.signals.base import Signal


@dataclass
class MetricSignal(Signal):
    """
    Triggered when a quantitative metric crosses a threshold.

    Example — latency regression:
        MetricSignal(
            description="p99 latency on /api/search regressed from 80ms to 340ms",
            metric_name="http.request.duration.p99",
            metric_path="/api/search",
            current_value=340.0,
            baseline_value=80.0,
            threshold=150.0,
            unit="ms",
            direction="lower_is_better",
        )
    """
    metric_name: str = ""
    metric_path: Optional[str] = None
    current_value: float = 0.0
    baseline_value: Optional[float] = None
    threshold: Optional[float] = None
    unit: str = ""
    direction: Literal["higher_is_better", "lower_is_better"] = "lower_is_better"
