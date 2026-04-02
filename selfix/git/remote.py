from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class RepoConfig:
    """Configuration for a remote git repository."""
    url: str                              # https or ssh git URL
    local_path: str                       # where to clone / find it locally
    auth_token: Optional[str] = None      # GitHub/GitLab PAT
    clone_depth: Optional[int] = 50       # shallow clone for speed; None = full


class RepoManager:
    """Manages the lifecycle of a remote repo: clone, fetch, push."""

    async def ensure_local(self, config: RepoConfig) -> str:
        """
        If repo already exists locally, fetch latest main.
        If not, clone it.
        Returns absolute path to local repo.
        """
        git_dir = os.path.join(config.local_path, ".git")
        if os.path.exists(git_dir):
            logger.info("Repo already cloned at %s — fetching latest", config.local_path)
            await self._fetch_latest(config)
        else:
            logger.info("Cloning %s → %s", config.url, config.local_path)
            await self._clone(config)
        return config.local_path

    async def _clone(self, config: RepoConfig) -> None:
        url = self._inject_token(config.url, config.auth_token)
        args = ["git", "clone", url, config.local_path]
        if config.clone_depth:
            args += ["--depth", str(config.clone_depth)]
        subprocess.run(args, check=True)

    async def _fetch_latest(self, config: RepoConfig) -> None:
        subprocess.run(
            ["git", "fetch", "origin"],
            cwd=config.local_path,
            check=True,
        )
        # Reset to origin/HEAD to pick up latest changes
        try:
            subprocess.run(
                ["git", "reset", "--hard", "origin/HEAD"],
                cwd=config.local_path,
                check=True,
            )
        except subprocess.CalledProcessError:
            # Fallback: try origin/main, then origin/master
            for branch in ("origin/main", "origin/master"):
                result = subprocess.run(
                    ["git", "reset", "--hard", branch],
                    cwd=config.local_path,
                )
                if result.returncode == 0:
                    break

    async def push_branch(self, repo_path: str, branch_name: str) -> None:
        """Push the fix branch to origin."""
        logger.info("Pushing branch %s to origin", branch_name)
        subprocess.run(
            ["git", "push", "origin", branch_name, "--set-upstream"],
            cwd=repo_path,
            check=True,
        )

    def _inject_token(self, url: str, token: Optional[str]) -> str:
        if not token:
            return url
        # https://token@github.com/org/repo.git
        return url.replace("https://", f"https://{token}@")
