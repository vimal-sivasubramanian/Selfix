from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import List, Literal

from selfix.validator.protocol import FixContext, SelfixValidator, ValidationResult


@dataclass
class CompositeValidator:
    """
    Runs multiple validators concurrently.
    mode="all"  → passes only if ALL pass (AND)
    mode="any"  → passes if ANY passes (OR)
    Feedback combines results from all validators.
    """
    validators: List[SelfixValidator]
    mode: Literal["all", "any"] = "all"

    async def validate(self, repo_path: str, context: FixContext) -> ValidationResult:
        results = await asyncio.gather(*[
            v.validate(repo_path, context) for v in self.validators
        ])

        if self.mode == "all":
            passed = all(r.passed for r in results)
        else:
            passed = any(r.passed for r in results)

        score = sum(r.score for r in results) / len(results)
        feedback = "\n\n".join([
            f"Validator {i + 1}: {'PASSED' if r.passed else 'FAILED'}\n{r.feedback}"
            for i, r in enumerate(results)
        ])

        return ValidationResult(
            passed=passed,
            score=score,
            feedback=feedback,
            metadata={"individual_results": [r.metadata for r in results]},
        )
