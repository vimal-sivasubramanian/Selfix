from selfix.git.pr import PRConfig, PRProvider, PRRequest, PRResult
from selfix.git.providers.github import GitHubPRProvider
from selfix.git.providers.gitlab import GitLabPRProvider
from selfix.git.remote import RepoConfig, RepoManager
from selfix.git.repo import (
    capture_base_commit,
    commit_changes,
    create_branch,
    current_branch,
    delete_branch,
    get_diff,
    revert_to_base,
    verify_repo,
)

__all__ = [
    # Local repo ops
    "verify_repo",
    "create_branch",
    "get_diff",
    "commit_changes",
    "delete_branch",
    "current_branch",
    "capture_base_commit",
    "revert_to_base",
    # Remote repo
    "RepoConfig",
    "RepoManager",
    # PR types
    "PRConfig",
    "PRProvider",
    "PRRequest",
    "PRResult",
    # PR providers
    "GitHubPRProvider",
    "GitLabPRProvider",
]
