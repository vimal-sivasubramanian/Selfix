from __future__ import annotations

import asyncio
import logging

from selfix.graph.state import PipelineState

logger = logging.getLogger(__name__)


def pr_creation_node(state: PipelineState) -> dict:
    """
    Push the fix branch to remote and open a pull request via the configured PRProvider.
    Falls back gracefully if no pr_provider is configured (local-only usage).
    """
    config = state["config"]

    if config.pr_provider is None:
        logger.info("pr_creation: no pr_provider configured — skipping PR creation")
        return {}

    # Run the async implementation synchronously (nodes are sync in LangGraph by default)
    return asyncio.get_event_loop().run_until_complete(_create_pr(state))


async def _create_pr(state: PipelineState) -> dict:
    from selfix.git.pr import PRRequest, build_pr_body, build_pr_title
    from selfix.git.remote import RepoManager

    config = state["config"]
    pr_config = config.pr_config
    signal = state["signal"]

    # Push branch to remote
    repo_manager = RepoManager()
    try:
        await repo_manager.push_branch(state["repo_path"], state["branch_name"])
    except Exception as exc:
        logger.error("Failed to push branch %s: %s", state["branch_name"], exc)
        return {"error": f"Branch push failed: {exc}"}

    # Build PR content
    title = build_pr_title(signal)
    body = build_pr_body(dict(state))

    repo_url = (
        config.repo_config.url
        if config.repo_config
        else _infer_remote_url(state["repo_path"])
    )

    request = PRRequest(
        repo_url=repo_url,
        base_branch=pr_config.base_branch,
        head_branch=state["branch_name"],
        title=title,
        body=body,
        labels=pr_config.labels,
        reviewers=pr_config.reviewers,
        draft=pr_config.draft,
    )

    result = await config.pr_provider.create_pull_request(request)
    logger.info("PR opened: %s", result.pr_url)

    return {
        "pr_url": result.pr_url,
        "pr_number": result.pr_number,
        "status": "success",
    }


def _infer_remote_url(repo_path: str) -> str:
    """Best-effort: read the origin URL from git config."""
    import subprocess
    result = subprocess.run(
        ["git", "remote", "get-url", "origin"],
        cwd=repo_path,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else ""
