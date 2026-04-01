from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from selfix.validator.protocol import FixContext, ValidationResult

logger = logging.getLogger(__name__)


@dataclass
class ShellCommandValidator:
    """
    The simplest possible validator: run a shell command, pass if exit code is 0.

    Examples:
        ShellCommandValidator("pytest tests/ -x -q")
        ShellCommandValidator("go test ./...")
        ShellCommandValidator("cargo test")
        ShellCommandValidator("npm test")
        ShellCommandValidator("python backtest.py --assert-sharpe 1.2")
    """
    command: str
    timeout_seconds: int = 300
    working_dir: str | None = None  # defaults to repo_path

    async def validate(self, repo_path: str, context: FixContext) -> ValidationResult:
        cwd = self.working_dir or repo_path
        logger.info("Running validator: %s (cwd=%s)", self.command, cwd)

        proc = await asyncio.create_subprocess_shell(
            self.command,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(
                proc.communicate(),
                timeout=self.timeout_seconds,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.communicate()
            return ValidationResult(
                passed=False,
                score=0.0,
                feedback=f"Validator timed out after {self.timeout_seconds}s",
                metadata={"command": self.command, "exit_code": None, "timed_out": True},
            )

        output = stdout.decode(errors="replace")
        passed = proc.returncode == 0

        logger.info("Validator exit_code=%d passed=%s", proc.returncode, passed)

        return ValidationResult(
            passed=passed,
            score=1.0 if passed else 0.0,
            feedback=output[-2000:],  # last 2000 chars — most relevant for retry
            metadata={
                "command": self.command,
                "exit_code": proc.returncode,
                "full_output": output,
            },
        )
