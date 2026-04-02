from __future__ import annotations

import asyncio
import logging
import subprocess

from selfix.graph.state import PipelineState
from selfix.validator.protocol import ValidationResult

logger = logging.getLogger(__name__)


def _run_shell(command: str, cwd: str, timeout: int) -> tuple[int, str]:
    """Run a shell command synchronously and return (returncode, combined output)."""
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=timeout,
        )
        output = result.stdout + result.stderr
        return result.returncode, output
    except subprocess.TimeoutExpired:
        return 1, f"Build command timed out after {timeout}s"


def build_check_node(state: PipelineState) -> dict:
    build_cmd = state["config"].build_command
    if not build_cmd:
        return {"build_check_output": "skipped"}

    repo_path = state["repo_path"]
    logger.info("Running build check: %s", build_cmd)

    returncode, output = _run_shell(build_cmd, cwd=repo_path, timeout=60)

    if returncode != 0:
        logger.info("Build check FAILED (exit %d)", returncode)
        return {
            "build_check_output": output,
            "validation_result": ValidationResult(
                passed=False,
                score=0.0,
                feedback=f"Build failed before validation could run:\n{output[-2000:]}",
                metadata={"build_command": build_cmd, "exit_code": returncode},
            ),
        }

    logger.info("Build check PASSED")
    return {"build_check_output": output}


def route_after_build_check(state: PipelineState) -> str:
    """Skip validation if build_check already populated a failed ValidationResult."""
    if state.get("validation_result") is not None:
        return "retry_decision"
    return "validation"
