from __future__ import annotations

from selfix.signals.base import Signal


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
    previous_feedback: str | None = None,
) -> str:
    feedback_section = (
        f"Previous attempt feedback:\n{previous_feedback}"
        if previous_feedback
        else "This is the first attempt."
    )
    return f"""You are fixing a code repository based on your earlier exploration.

Signal: {signal.description}
Repo path: {repo_path}

Exploration summary:
{exploration_summary}

{feedback_section}

Apply the fix now. Edit only the files identified in your exploration.
After editing, produce:
1. A brief explanation of exactly what you changed and why
2. Confirm the diff is complete

Be surgical. Do not rewrite files unnecessarily. Do not add comments, docstrings,
or refactoring beyond what is required to address the signal."""
