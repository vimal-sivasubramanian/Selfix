from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from selfix.validator.protocol import FixContext, ValidationResult

logger = logging.getLogger(__name__)


@dataclass
class HttpHealthValidator:
    """
    Starts a process, waits for an HTTP health endpoint to respond,
    then tears the process down.
    Useful for service-level validation after a fix.
    """
    start_command: str          # e.g. "uvicorn app:main --port 8080"
    health_url: str             # e.g. "http://localhost:8080/health"
    expected_status: int = 200
    startup_timeout: int = 30
    request_timeout: int = 10

    async def validate(self, repo_path: str, context: FixContext) -> ValidationResult:
        try:
            import httpx
        except ImportError:
            return ValidationResult(
                passed=False,
                score=0.0,
                feedback="httpx is required for HttpHealthValidator. Install it with: pip install httpx",
                metadata={"url": self.health_url},
            )

        proc = await asyncio.create_subprocess_shell(
            self.start_command,
            cwd=repo_path,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            passed, feedback = await self._wait_and_check()
        finally:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=5)
            except asyncio.TimeoutError:
                proc.kill()

        return ValidationResult(
            passed=passed,
            score=1.0 if passed else 0.0,
            feedback=feedback,
            metadata={"url": self.health_url, "start_command": self.start_command},
        )

    async def _wait_and_check(self) -> tuple[bool, str]:
        import httpx

        deadline = asyncio.get_event_loop().time() + self.startup_timeout
        last_error = "Service did not start in time"

        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(1)
            try:
                async with httpx.AsyncClient(timeout=self.request_timeout) as client:
                    response = await client.get(self.health_url)
                    if response.status_code == self.expected_status:
                        return True, f"Health check passed: HTTP {response.status_code}"
                    last_error = f"Expected HTTP {self.expected_status}, got {response.status_code}"
            except Exception as exc:
                last_error = str(exc)

        return False, f"Health check failed: {last_error}"
