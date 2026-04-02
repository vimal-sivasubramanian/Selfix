from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import List, Optional

from selfix.validator.protocol import FixContext, ValidationResult


@dataclass
class PytestValidator:
    """
    Runs pytest and passes if all tests pass.
    Optionally enforces a minimum coverage threshold.
    """
    test_path: str = "tests/"
    min_coverage: Optional[float] = None    # e.g. 0.80 for 80%
    extra_args: List[str] = field(default_factory=list)
    timeout_seconds: int = 300

    async def validate(self, repo_path: str, context: FixContext) -> ValidationResult:
        args = ["pytest", self.test_path, "-x", "-q", "--tb=short"]
        if self.min_coverage is not None:
            args += [
                f"--cov={repo_path}",
                f"--cov-fail-under={int(self.min_coverage * 100)}",
            ]
        args += self.extra_args

        cmd = " ".join(args)
        try:
            proc = await asyncio.create_subprocess_shell(
                cmd,
                cwd=repo_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            stdout, _ = await asyncio.wait_for(
                proc.communicate(),
                timeout=self.timeout_seconds,
            )
            output = stdout.decode()
            passed = proc.returncode == 0
            return ValidationResult(
                passed=passed,
                score=1.0 if passed else 0.0,
                feedback=output[-2000:],
                metadata={"command": cmd, "exit_code": proc.returncode},
            )
        except asyncio.TimeoutError:
            return ValidationResult(
                passed=False,
                score=0.0,
                feedback=f"pytest timed out after {self.timeout_seconds}s",
                metadata={"command": cmd, "timed_out": True},
            )
