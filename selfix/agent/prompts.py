from __future__ import annotations

from typing import TYPE_CHECKING, List, Optional

from selfix.signals.base import Signal

if TYPE_CHECKING:
    from selfix.attempt import AttemptRecord


def exploration_prompt(signal: Signal, repo_path: str) -> str:
    scope = signal.scope_hint or "the entire repository"
    return f"""You are exploring a code repository to understand a reported problem or improvement opportunity.

Signal: {signal.description}
Scope hint: {scope}
Repo path: {repo_path}

Tasks:
1. Understand the repository structure (languages, frameworks, entry points)
2. Locate the code most relevant to the signal
3. Understand the current implementation and why it is suboptimal
4. Identify all files that will need to change

Return a structured exploration summary covering:
- Relevant files (paths and purpose)
- Root cause or improvement opportunity
- Proposed approach for the fix
- Risks or considerations

Use only Read, Glob, and Grep tools — do not edit any files during exploration."""


def fix_generation_prompt(
    signal: Signal,
    exploration_summary: str,
    repo_path: str,
    attempt_number: int = 1,
    max_attempts: int = 3,
    attempt_history: Optional[List["AttemptRecord"]] = None,
    current_feedback: Optional[str] = None,
) -> str:
    if not attempt_history:
        history_section = "This is the first attempt."
    else:
        lines = ["--- Attempt History ---"]
        for record in attempt_history:
            lines.append(f"\nAttempt {record.attempt_number}:")
            lines.append(f"  What was changed: {record.agent_reasoning[:500] if record.agent_reasoning else '(no reasoning)'}")
            if record.diff:
                lines.append(f"  Diff applied:\n{record.diff[:1500]}")
            vr = record.validation_result
            if vr:
                lines.append(f"  Validation feedback:\n    {vr.feedback[:500] if vr.feedback else '(none)'}")
        history_section = "\n".join(lines)

    if current_feedback and attempt_history:
        task_section = f"""--- Your Task ---
The previous attempt(s) did not pass validation.
Study the feedback carefully. Do not repeat the same approach.

Key guidance from last validation:
{current_feedback}

Apply a revised fix now."""
    else:
        task_section = "Apply the fix now. Edit only the files identified in your exploration."

    return f"""You are fixing a code repository based on your earlier exploration.
This is attempt {attempt_number} of {max_attempts}.

Signal: {signal.description}
Repo path: {repo_path}

Exploration summary:
{exploration_summary}

{history_section}

{task_section}

After editing, produce:
1. A brief explanation of exactly what you changed and why
2. Confirm the diff is complete

Be surgical. Do not rewrite files unnecessarily. Do not add comments, docstrings,
or refactoring beyond what is required to address the signal."""
