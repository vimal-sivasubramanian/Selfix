from __future__ import annotations

import logging
from datetime import datetime
from urllib.parse import urlparse

from selfix.git.pr import PRRequest, PRResult

logger = logging.getLogger(__name__)


class GitHubPRProvider:
    """GitHub REST API adapter for opening pull requests."""

    def __init__(self, token: str):
        self.token = token
        self.base_url = "https://api.github.com"

    async def create_pull_request(self, request: PRRequest) -> PRResult:
        import aiohttp

        owner, repo = self._parse_repo_url(request.repo_url)
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

        async with aiohttp.ClientSession() as session:
            # Create PR
            resp = await session.post(
                f"{self.base_url}/repos/{owner}/{repo}/pulls",
                headers=headers,
                json={
                    "title": request.title,
                    "body": request.body,
                    "head": request.head_branch,
                    "base": request.base_branch,
                    "draft": request.draft,
                },
            )
            resp.raise_for_status()
            data = await resp.json()

            pr_number = data["number"]
            pr_url = data["html_url"]
            logger.info("GitHub PR created: %s", pr_url)

            # Add labels
            if request.labels:
                await self._add_labels(session, headers, owner, repo, pr_number, request.labels)

            # Request reviewers
            if request.reviewers:
                await self._request_reviewers(session, headers, owner, repo, pr_number, request.reviewers)

        return PRResult(
            pr_url=pr_url,
            pr_number=pr_number,
            created_at=datetime.utcnow(),
        )

    async def _add_labels(self, session, headers, owner, repo, pr_number, labels):
        import aiohttp

        resp = await session.post(
            f"{self.base_url}/repos/{owner}/{repo}/issues/{pr_number}/labels",
            headers=headers,
            json={"labels": labels},
        )
        if resp.status >= 400:
            logger.warning("Failed to add labels to PR #%d: %s", pr_number, await resp.text())

    async def _request_reviewers(self, session, headers, owner, repo, pr_number, reviewers):
        resp = await session.post(
            f"{self.base_url}/repos/{owner}/{repo}/pulls/{pr_number}/requested_reviewers",
            headers=headers,
            json={"reviewers": reviewers},
        )
        if resp.status >= 400:
            logger.warning("Failed to request reviewers for PR #%d: %s", pr_number, await resp.text())

    def _parse_repo_url(self, url: str) -> tuple[str, str]:
        """Extract owner/repo from a GitHub URL."""
        parsed = urlparse(url)
        path = parsed.path.strip("/").removesuffix(".git")
        parts = path.split("/")
        if len(parts) < 2:
            raise ValueError(f"Cannot parse owner/repo from URL: {url}")
        return parts[0], parts[1]
