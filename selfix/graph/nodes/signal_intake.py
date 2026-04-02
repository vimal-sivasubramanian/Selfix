from __future__ import annotations

import logging

from selfix.graph.state import PipelineState

logger = logging.getLogger(__name__)


def signal_intake_node(state: PipelineState) -> dict:
    config = state["config"]
    signal = config.signal

    logger.info("=" * 60)
    logger.info("Selfix run started")
    logger.info("  Signal ID  : %s", signal.id)
    logger.info("  Description: %s", signal.description[:120])
    logger.info("  Scope hint : %s", signal.scope_hint or "(none)")
    logger.info("=" * 60)

    # Resolve repo_path — support both local path and remote RepoConfig
    repo_path = config.repo_path
    if repo_path is None and config.repo_config is not None:
        # Remote repo: will be cloned/fetched in repo_setup_node
        repo_path = config.repo_config.local_path

    # Build signal-type-specific focus hint for the agent
    agent_focus_hint = _build_focus_hint(signal)
    if agent_focus_hint:
        logger.info("  Focus hint : %s", agent_focus_hint[:120])

    return {
        "signal": signal,
        "repo_path": repo_path,
        "attempt_number": 1,
        "attempt_history": [],
        "status": "running",
        "error": None,
        "branch_name": None,
        "exploration_summary": None,
        "fix_diff": None,
        "agent_reasoning": None,
        "validation_result": None,
        "current_feedback": None,
        "agent_focus_hint": agent_focus_hint,
        "pr_url": None,
        "pr_number": None,
    }


def _build_focus_hint(signal) -> str | None:
    from selfix.signals.error import ErrorSignal
    from selfix.signals.metric import MetricSignal
    from selfix.signals.scheduled import ScheduledSignal

    if isinstance(signal, ErrorSignal):
        parts = []
        if signal.file_hint:
            parts.append(f"Focus on {signal.file_hint}")
            if signal.line_hint:
                parts[-1] += f" line {signal.line_hint}"
        if signal.error_type:
            parts.append(f"Error type: {signal.error_type}")
        if signal.stack_trace:
            parts.append("Stack trace provided.")
        if signal.frequency:
            parts.append(f"Seen {signal.frequency} time(s) recently.")
        return " ".join(parts) if parts else None

    elif isinstance(signal, MetricSignal):
        direction = signal.direction.replace("_", " ")
        hint = f"Metric '{signal.metric_name}'"
        if signal.metric_path:
            hint += f" at '{signal.metric_path}'"
        if signal.baseline_value is not None:
            hint += f" regressed from {signal.baseline_value}{signal.unit}"
        hint += f" to {signal.current_value}{signal.unit}."
        if signal.threshold is not None:
            hint += f" Target: {direction} than {signal.threshold}{signal.unit}."
        return hint

    elif isinstance(signal, ScheduledSignal):
        scope = signal.scope_hint or "entire repo"
        return f"Proactive {signal.improvement_type} scan across {scope}."

    return None
