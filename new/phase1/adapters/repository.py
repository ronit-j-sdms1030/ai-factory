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

    def list_open_pull_requests(self) -> list:
        return []

    def list_reviews(self, number: int) -> list:
        del number
        return []


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

    def _base_branch(self) -> str:
        cached = getattr(self, "_default_branch", None)
        if cached:
            return cached
        self._ensure_base()
        return self._default_branch

    def _already_there(self, exc: RuntimeError) -> bool:
        text = str(exc)
        return any(token in text for token in ("422", "409", "Reference already exists", "A pull request already exists"))

    def _ensure_base(self) -> None:
        """PRs merge onto ``main`` even if GitHub's default is a leftover req branch.

        Changing the repo default needs admin. Creating ``main`` and targeting it
        only needs contents + pull-request permission.
        """
        try:
            self._api("GET", "/git/ref/heads/main")
        except RuntimeError as exc:
            if ": 404 " not in str(exc):
                raise
            info = self._api("GET", "")
            current = str(info.get("default_branch") or "main")
            if current == "main":
                self.commit_remote_file(
                    "main",
                    "README.md",
                    "# Governance\n\nArtefacts for the governed factory.\n",
                    "seed default branch",
                )
            else:
                tip = self._api("GET", f"/git/ref/heads/{current}")
                self._api(
                    "POST",
                    "/git/refs",
                    {"ref": "refs/heads/main", "sha": tip["object"]["sha"]},
                )
        try:
            self._api("PATCH", "", {"name": self.repo, "default_branch": "main"})
        except RuntimeError:
            pass
        self._default_branch = "main"

    def commit_files(
        self,
        branch: str,
        files: dict[str, str],
        message: str,
        *,
        author: str,
        email: str,
    ) -> str:
        sha = super().commit_files(
            branch, files, message, author=author, email=email
        )
        self._ensure_base()
        if branch != "main":
            try:
                self.create_branch(branch)
            except RuntimeError as exc:
                if not self._already_there(exc):
                    raise
        for path, content in files.items():
            self.commit_remote_file(branch, path, content, message)
        return sha

    def merge_to_main(self, branch: str, message: str) -> str:
        sha = super().merge_to_main(branch, message)
        if branch == "main":
            return sha
        number = None
        try:
            number = self.open_pull_request(branch, message)
        except RuntimeError as exc:
            if not self._already_there(exc):
                raise
            pulls = self._api("GET", f"/pulls?head={self.owner}:{branch}&state=open")
            if pulls:
                number = int(pulls[0]["number"])
        if number is None:
            return sha
        try:
            self.merge_pull_request(number, message)
        except RuntimeError as exc:
            if "409" not in str(exc) and "not mergeable" not in str(exc).lower():
                raise
        return sha

    def create_branch(self, branch: str, *, base: str | None = None) -> str:
        ref = self._api("GET", f"/git/ref/heads/{base or self._base_branch()}")
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
            "POST", "/pulls", {"head": branch, "base": self._base_branch(), "title": title, "body": body}
        )
        return int(result["number"])

    def merge_pull_request(self, number: int, message: str = "") -> str:
        result = self._api(
            "PUT", f"/pulls/{number}/merge", {"commit_message": message}
        )
        if not result.get("merged"):
            raise RuntimeError(str(result.get("message") or "GitHub refused merge"))
        return str(result["sha"])

    def list_open_pull_requests(self) -> list:
        return self._api("GET", "/pulls?state=open&per_page=50") or []

    def list_reviews(self, number: int) -> list:
        return self._api("GET", f"/pulls/{int(number)}/reviews") or []
