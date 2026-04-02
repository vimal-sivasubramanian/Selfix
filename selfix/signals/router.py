from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Callable, Optional

if TYPE_CHECKING:
    from selfix.config import SelfixConfig
    from selfix.result import SelfixResult
    from selfix.signals.base import Signal
    from selfix.signals.error import ErrorSignal
    from selfix.signals.metric import MetricSignal
    from selfix.signals.scheduled import ScheduledSignal

logger = logging.getLogger(__name__)


class SignalRouter:
    """
    Receives signals from any source, deduplicates, and dispatches pipeline runs.
    Can be used standalone or via the webhook listener.
    """

    def __init__(
        self,
        config_factory: Callable[["Signal"], "SelfixConfig"],
        dedup_window_seconds: int = 300,
    ):
        self.config_factory = config_factory
        self._dedup_window_seconds = dedup_window_seconds
        self._seen: dict[str, datetime] = {}

    async def dispatch(self, signal: "Signal") -> Optional["SelfixResult"]:
        import selfix

        fingerprint = self._fingerprint(signal)

        if self._is_duplicate(fingerprint):
            logger.info(
                "Duplicate signal suppressed (fingerprint=%s)", fingerprint[:12]
            )
            return None

        self._seen[fingerprint] = datetime.utcnow()
        logger.info("Dispatching signal: %s", signal.description[:80])
        config = self.config_factory(signal)
        return await selfix.run(config)

    def _fingerprint(self, signal: "Signal") -> str:
        from selfix.signals.error import ErrorSignal
        from selfix.signals.metric import MetricSignal
        from selfix.signals.scheduled import ScheduledSignal

        if isinstance(signal, ErrorSignal):
            key = f"error:{signal.error_type}:{signal.file_hint}:{signal.line_hint}"
        elif isinstance(signal, MetricSignal):
            key = f"metric:{signal.metric_name}:{signal.metric_path}"
        elif isinstance(signal, ScheduledSignal):
            # Include the date so the same cron fires once per day max
            today = datetime.utcnow().date().isoformat()
            key = f"scheduled:{signal.cron}:{signal.improvement_type}:{today}"
        else:
            # ManualSignal and unknowns: fingerprint by description
            key = f"manual:{signal.description}"

        return hashlib.sha256(key.encode()).hexdigest()

    def _is_duplicate(self, fingerprint: str) -> bool:
        last = self._seen.get(fingerprint)
        if not last:
            return False
        elapsed = (datetime.utcnow() - last).total_seconds()
        return elapsed < self._dedup_window_seconds
