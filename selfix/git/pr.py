from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Protocol, runtime_checkable


@dataclass
class PRRequest:
    repo_url: str
    base_branch: str        # usually "main" or "master"
    head_branch: str        # the selfix/fix-* branch
    title: str
    body: str
    labels: List[str]
    reviewers: List[str]
    draft: bool


@dataclass
class PRResult:
    pr_url: str
    pr_number: int
    created_at: datetime


@dataclass
class PRConfig:
    base_branch: str = "main"
    labels: List[str] = field(default_factory=lambda: ["selfix", "automated"])
    reviewers: List[str] = field(default_factory=list)
    draft: bool = False
    auto_merge: bool = False


@runtime_checkable
class PRProvider(Protocol):
    async def create_pull_request(self, request: PRRequest) -> PRResult:
        ...


# ── PR body / title helpers ───────────────────────────────────────────────────

def build_pr_title(signal: object) -> str:
    from selfix.signals.error import ErrorSignal
    from selfix.signals.metric import MetricSignal
    from selfix.signals.scheduled import ScheduledSignal

    desc = getattr(signal, "description", "")
    if isinstance(signal, ErrorSignal):
        prefix = f"fix({signal.error_type or 'error'})" if signal.error_type else "fix"
    elif isinstance(signal, MetricSignal):
        prefix = f"perf({signal.metric_name})" if signal.metric_name else "perf"
    elif isinstance(signal, ScheduledSignal):
        prefix = f"chore({signal.improvement_type})"
    else:
        prefix = "fix"

    # Keep title under 72 chars
    summary = desc[:60] + "…" if len(desc) > 60 else desc
    return f"{prefix}: {summary}"


def build_pr_body(state: dict) -> str:
    signal = state.get("signal")
    attempt_history = state.get("attempt_history", [])
    validation_result = state.get("validation_result")
    agent_reasoning = state.get("agent_reasoning") or ""
    fix_diff = state.get("fix_diff") or ""
    branch_name = state.get("branch_name") or ""
    attempt_number = state.get("attempt_number", 1)

    # Signal type label
    from selfix.signals.error import ErrorSignal
    from selfix.signals.metric import MetricSignal
    from selfix.signals.scheduled import ScheduledSignal

    if isinstance(signal, ErrorSignal):
        signal_type = "ErrorSignal"
    elif isinstance(signal, MetricSignal):
        signal_type = "MetricSignal"
    elif isinstance(signal, ScheduledSignal):
        signal_type = "ScheduledSignal"
    else:
        signal_type = "ManualSignal"

    created_at = getattr(signal, "created_at", "")
    description = getattr(signal, "description", "")

    # Validation section
    if validation_result:
        score = getattr(validation_result, "score", 0.0)
        val_feedback = getattr(validation_result, "feedback", "")
        val_section = (
            f"**Score:** {score:.2f}\n"
            f"**Output:**\n```\n{val_feedback[-1000:]}\n```"
        )
        val_status = "✅ PASSED"
    else:
        val_section = "_No validation result available._"
        val_status = "—"

    # Diff stats (simple line count)
    additions = fix_diff.count("\n+") if fix_diff else 0
    deletions = fix_diff.count("\n-") if fix_diff else 0
    files_changed = fix_diff.count("\ndiff --git") if fix_diff else 0

    # Attempt history table
    if len(attempt_history) > 1:
        rows = []
        for rec in attempt_history:
            result = getattr(rec, "validation_result", None)
            passed = getattr(result, "passed", False) if result else False
            feedback = getattr(result, "feedback", "") if result else ""
            icon = "✅ Passed" if passed else "❌ Failed"
            summary = feedback[:100].replace("\n", " ") if not passed else "—"
            rows.append(f"| {rec.attempt_number} | {icon} | {summary} |")
        history_section = (
            "## Attempt History\n\n"
            "| Attempt | Result | Feedback Summary |\n"
            "|---------|--------|------------------|\n"
            + "\n".join(rows)
        )
    else:
        history_section = ""

    body = f"""\
## Selfix Autonomous Fix

**Signal:** {description}
**Triggered by:** {signal_type} at {created_at}
**Attempts:** {attempt_number}
**Validation:** {val_status}

---

## What Changed

{agent_reasoning}

---

## Diff Summary

{files_changed} file(s) changed, {additions} insertion(s), {deletions} deletion(s)

---

## Validation Report

{val_section}

{history_section}

---
*Opened automatically by [Selfix](https://github.com/vimal-sivasubramanian/selfix)*
"""
    return body.strip()
