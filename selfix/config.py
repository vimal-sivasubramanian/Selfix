from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from selfix.signals.base import Signal
    from selfix.validator.protocol import SelfixValidator


@dataclass
class AgentConfig:
    model: str = "claude-opus-4-6"
    max_tokens: int = 8192
    allowed_tools: list[str] = field(default_factory=lambda: [
        "Read", "Glob", "Grep", "Edit", "Bash"
    ])
    permission_mode: str = "bypassPermissions"


@dataclass
class SelfixConfig:
    repo_path: str
    signal: "Signal"
    validator: "SelfixValidator"
    max_attempts: int = 3
    agent_config: AgentConfig = field(default_factory=AgentConfig)
    checkpoint_dir: str = ".selfix/checkpoints"
