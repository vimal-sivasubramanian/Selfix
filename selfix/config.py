from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Awaitable, Callable, Optional

if TYPE_CHECKING:
    from selfix.git.pr import PRConfig, PRProvider
    from selfix.git.remote import RepoConfig
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
    # Repo — at least one of repo_path or repo_config must be set
    repo_path: Optional[str] = None           # local path (Phase 1/2 style)
    repo_config: Optional["RepoConfig"] = None  # remote repo (Phase 3)

    # Core
    signal: Optional["Signal"] = None
    validator: Optional["SelfixValidator"] = None
    max_attempts: int = 3
    agent_config: AgentConfig = field(default_factory=AgentConfig)
    checkpoint_dir: str = ".selfix/checkpoints"

    # Phase 2
    build_command: Optional[str] = None
    escalation_handler: Optional[Callable[["EscalationEvent"], Awaitable[None]]] = None

    # Phase 3 — PR
    pr_config: "PRConfig" = field(default_factory=lambda: _default_pr_config())
    pr_provider: Optional["PRProvider"] = None  # required for PR creation

    def __post_init__(self):
        if self.repo_path is None and self.repo_config is None:
            raise ValueError("Either repo_path or repo_config must be set")


def _default_pr_config():
    from selfix.git.pr import PRConfig
    return PRConfig()


@dataclass
class EscalationEvent:
    signal: "Signal"
    attempts: list
    branch_name: Optional[str]
