from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import git

logger = logging.getLogger(__name__)


def capture_base_commit(path: str) -> str:
    """Return the current HEAD SHA (recorded before any edits on the fix branch)."""
    result = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=path,
    )
    return result.decode().strip()


def revert_to_base(path: str, base_commit: str) -> None:
    """
    Hard-reset the working tree and index back to base_commit.
    Preserves the branch — only file state is reset.
    """
    subprocess.run(
        ["git", "reset", "--hard", base_commit],
        cwd=path,
        check=True,
    )
    logger.info("Reverted repo to base commit %s", base_commit[:8])


def verify_repo(path: str) -> git.Repo:
    """Raise if path is not a valid git repository."""
    p = Path(path)
    if not p.exists():
        raise ValueError(f"Repo path does not exist: {path}")
    try:
        repo = git.Repo(path, search_parent_directories=False)
        return repo
    except git.InvalidGitRepositoryError:
        raise ValueError(f"Not a git repository: {path}")


def create_branch(path: str, name: str) -> None:
    """Create and checkout a new branch."""
    repo = verify_repo(path)
    logger.info("Creating branch: %s", name)
    repo.git.checkout("-b", name)


def get_diff(path: str) -> str:
    """Return the unified diff of all uncommitted changes vs HEAD."""
    result = subprocess.run(
        ["git", "diff", "HEAD"],
        cwd=path,
        capture_output=True,
        text=True,
    )
    return result.stdout


def commit_changes(path: str, message: str) -> str | None:
    """Stage all changes and commit. Returns the commit sha, or None if nothing to commit."""
    repo = verify_repo(path)
    repo.git.add("-A")
    if not repo.index.diff("HEAD") and not repo.untracked_files:
        logger.info("Nothing to commit.")
        return None
    commit = repo.index.commit(message)
    logger.info("Committed: %s", commit.hexsha[:8])
    return commit.hexsha


def delete_branch(path: str, name: str) -> None:
    """Delete a local branch (used for cleanup on failure)."""
    repo = verify_repo(path)
    current = repo.active_branch.name
    if current == name:
        # Switch to default branch before deleting
        default = _default_branch(repo)
        repo.git.checkout(default)
    repo.git.branch("-D", name)
    logger.info("Deleted branch: %s", name)


def current_branch(path: str) -> str:
    repo = verify_repo(path)
    return repo.active_branch.name


def _default_branch(repo: git.Repo) -> str:
    for name in ("main", "master"):
        try:
            repo.heads[name]
            return name
        except IndexError:
            continue
    return repo.heads[0].name
