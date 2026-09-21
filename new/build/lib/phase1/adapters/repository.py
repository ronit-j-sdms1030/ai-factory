"""Local Git and GitHub App repository adapters."""

from __future__ import annotations

import base64
import json
from urllib import error, request

from phase1.gitrepo import GovernanceRepo


class LocalGitRepository(GovernanceRepo):
    def create_branch(self, branch: str, *, base: str = "main") -> str:
        if self._run(["git", "branch", "--list", branch]).strip():
            return self.rev_parse(branch)
        self._run(["git", "branch", branch, base])
        return self.rev_parse(branch)

    def commit_remote_file(
        self, branch: str, path: str, content: str, message: str
    ) -> str:
        return self.commit_files(
            branch,
            {path: content},
            message,
            author="platform",
            email="platform@local",
        )

    def open_pull_request(self, branch: str, title: str, body: str = "") -> int:
        del branch, title, body
        raise NotImplementedError("local Git has no pull-request service")

    def merge_pull_request(self, number: int, message: str = "") -> str:
        del number, message
        raise NotImplementedError("local Git has no pull-request service")


class GitHubAppRepository(LocalGitRepository):
    """Local working tree plus GitHub App branch/commit/PR/merge primitives."""

    def __init__(self, root, *, owner: str, repo: str, token: str, api_url: str):
        super().__init__(root)
        self.owner = owner
        self.repo = repo
        self.token = token
        self.api_url = api_url.rstrip("/")

    def _api(self, method: str, path: str, payload: dict | None = None):
        req = request.Request(
            f"{self.api_url}/repos/{self.owner}/{self.repo}{path}",
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
                "Content-Type": "application/json",
            },
            method=method,
        )
        try:
            with request.urlopen(req, timeout=15) as response:
                return json.load(response)
        except error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            raise RuntimeError(f"GitHub API {method} {path}: {exc.code} {detail}") from exc

    def create_branch(self, branch: str, *, base: str = "main") -> str:
        ref = self._api("GET", f"/git/ref/heads/{base}")
        sha = ref["object"]["sha"]
        self._api("POST", "/git/refs", {"ref": f"refs/heads/{branch}", "sha": sha})
        return str(sha)

    def commit_remote_file(
        self, branch: str, path: str, content: str, message: str
    ) -> str:
        existing = None
        try:
            existing = self._api("GET", f"/contents/{path}?ref={branch}")
        except RuntimeError as exc:
            if ": 404 " not in str(exc):
                raise
        payload = {
            "message": message,
            "content": base64.b64encode(content.encode()).decode(),
            "branch": branch,
        }
        if existing:
            payload["sha"] = existing["sha"]
        result = self._api("PUT", f"/contents/{path}", payload)
        return str(result["commit"]["sha"])

    def open_pull_request(self, branch: str, title: str, body: str = "") -> int:
        result = self._api(
            "POST", "/pulls", {"head": branch, "base": "main", "title": title, "body": body}
        )
        return int(result["number"])

    def merge_pull_request(self, number: int, message: str = "") -> str:
        result = self._api(
            "PUT", f"/pulls/{number}/merge", {"commit_message": message}
        )
        if not result.get("merged"):
            raise RuntimeError(str(result.get("message") or "GitHub refused merge"))
        return str(result["sha"])
