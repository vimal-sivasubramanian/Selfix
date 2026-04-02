from __future__ import annotations

import logging
from datetime import datetime, timezone

import selfix.git as git_ops
from selfix.graph.state import PipelineState

logger = logging.getLogger(__name__)


def repo_setup_node(state: PipelineState) -> dict:
    import asyncio

    repo_path = state["repo_path"]
    signal = state["signal"]
    config = state["config"]

    # Phase 3: clone/fetch remote repo if configured
    if config.repo_config is not None:
        from selfix.git.remote import RepoManager
        repo_manager = RepoManager()
        repo_path = asyncio.get_event_loop().run_until_complete(
            repo_manager.ensure_local(config.repo_config)
        )

    git_ops.verify_repo(repo_path)

    ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
    branch_name = f"selfix/fix-{signal.id[:8]}-{ts}"

    logger.info("Creating fix branch: %s", branch_name)
    git_ops.create_branch(repo_path, branch_name)

    base_commit = git_ops.capture_base_commit(repo_path)
    logger.info("Base commit: %s", base_commit[:8])

    return {"branch_name": branch_name, "base_commit": base_commit}
