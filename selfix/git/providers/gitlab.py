from __future__ import annotations

import logging
from datetime import datetime
from urllib.parse import quote, urlparse

from selfix.git.pr import PRRequest, PRResult

logger = logging.getLogger(__name__)


class GitLabPRProvider:
    """
    GitLab REST API adapter for opening merge requests.
    Works against gitlab.com or self-hosted GitLab instances.
    """

    def __init__(self, token: str, base_url: str = "https://gitlab.com"):
        self.token = token
        self.base_url = base_url.rstrip("/")

    async def create_pull_request(self, request: PRRequest) -> PRResult:
        import aiohttp

        project_path = self._parse_project_path(request.repo_url)
        encoded_path = quote(project_path, safe="")
        headers = {
            "PRIVATE-TOKEN": self.token,
            "Content-Type": "application/json",
        }

        async with aiohttp.ClientSession() as session:
            resp = await session.post(
                f"{self.base_url}/api/v4/projects/{encoded_path}/merge_requests",
                headers=headers,
                json={
                    "title": request.title,
                    "description": request.body,
                    "source_branch": request.head_branch,
                    "target_branch": request.base_branch,
                    "draft": request.draft,
                    "reviewer_ids": [],  # reviewers resolved separately if needed
                    "labels": ",".join(request.labels),
                },
            )
            resp.raise_for_status()
            data = await resp.json()

        mr_url = data["web_url"]
        mr_number = data["iid"]
        logger.info("GitLab MR created: %s", mr_url)

        return PRResult(
            pr_url=mr_url,
            pr_number=mr_number,
            created_at=datetime.utcnow(),
        )

    def _parse_project_path(self, url: str) -> str:
        """Extract namespace/project from a GitLab URL."""
        parsed = urlparse(url)
        return parsed.path.strip("/").removesuffix(".git")
