"""Local Git and GitHub App repository adapters."""

from __future__ import annotations

import base64
import json
import re
import sys
from urllib import error, request

from phase1.gitrepo import GovernanceRepo

_REQ_ID = re.compile(r"REQ-\d+", re.I)


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
    """Local working tree plus GitHub App branch/commit/PR/merge primitives.

    With ``per_requirement`` every ``REQ-nnnn`` branch lives in its own private
    repo ``<repo>-req-nnnn``, created on first commit. Branches without a
    requirement id (skills, settings) stay in the governance repo.
    """

    def __init__(
        self,
        root,
        *,
        owner: str,
        repo: str,
        token: str,
        api_url: str,
        per_requirement: bool = False,
    ):
        super().__init__(root)
        self.owner = owner
        self.repo = repo
        self.token = token
        self.api_url = api_url.rstrip("/")
        self.per_requirement = per_requirement
        self._default_branches: dict[str, str] = {}
        self._known_repos: set[str] = {repo}
        self._refused: set[str] = set()

    def repo_for(self, branch: str) -> str:
        if not self.per_requirement:
            return self.repo
        found = _REQ_ID.search(branch or "")
        if not found:
            return self.repo
        name = f"{self.repo}-{found.group(0).lower()}"
        return self.repo if name in self._refused else name

    def _call(self, method: str, url: str, payload: dict | None = None):
        req = request.Request(
            url,
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
            path = url[len(self.api_url):]
            raise RuntimeError(f"GitHub API {method} {path}: {exc.code} {detail}") from exc

    def _api(self, method: str, path: str, payload: dict | None = None, *, repo: str | None = None):
        return self._call(
            method, f"{self.api_url}/repos/{self.owner}/{repo or self.repo}{path}", payload
        )

    def _ensure_repo(self, repo: str) -> None:
        if repo in self._known_repos:
            return
        try:
            self._api("GET", "", repo=repo)
        except RuntimeError as exc:
            if ": 404 " not in str(exc):
                raise
            me = self._call("GET", f"{self.api_url}/user")
            create = (
                f"{self.api_url}/user/repos"
                if str(me.get("login") or "").lower() == self.owner.lower()
                else f"{self.api_url}/orgs/{self.owner}/repos"
            )
            requirement = repo[len(self.repo) + 1 :].upper()
            try:
                self._call(
                    "POST",
                    create,
                    {
                        "name": repo,
                        "private": True,
                        "auto_init": True,
                        "description": f"Governed factory artefacts for {requirement}",
                    },
                )
            except RuntimeError as create_exc:
                if not self._already_there(create_exc):
                    raise
        self._known_repos.add(repo)

    def _base_branch(self, repo: str | None = None) -> str:
        repo = repo or self.repo
        cached = self._default_branches.get(repo)
        if cached:
            return cached
        self._ensure_base(repo)
        return self._default_branches[repo]

    def _already_there(self, exc: RuntimeError) -> bool:
        text = str(exc)
        return any(
            token in text
            for token in (
                "422",
                "409",
                "Reference already exists",
                "A pull request already exists",
                "name already exists",
            )
        )

    def _ensure_base(self, repo: str | None = None) -> None:
        """PRs merge onto ``main`` even if GitHub's default is a leftover req branch.

        Changing the repo default needs admin. Creating ``main`` and targeting it
        only needs contents + pull-request permission.
        """
        repo = repo or self.repo
        if repo in self._default_branches:
            return
        try:
            self._api("GET", "/git/ref/heads/main", repo=repo)
        except RuntimeError as exc:
            if ": 404 " not in str(exc):
                raise
            info = self._api("GET", "", repo=repo)
            current = str(info.get("default_branch") or "main")
            if current == "main":
                self._put_file(
                    repo,
                    "main",
                    "README.md",
                    "# Governance\n\nArtefacts for the governed factory.\n",
                    "seed default branch",
                )
            else:
                tip = self._api("GET", f"/git/ref/heads/{current}", repo=repo)
                self._api(
                    "POST",
                    "/git/refs",
                    {"ref": "refs/heads/main", "sha": tip["object"]["sha"]},
                    repo=repo,
                )
        try:
            self._api("PATCH", "", {"name": repo, "default_branch": "main"}, repo=repo)
        except RuntimeError:
            pass
        self._default_branches[repo] = "main"

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
        repo = self.repo_for(branch)
        try:
            self._ensure_repo(repo)
        except RuntimeError as exc:
            # Token cannot create repos: keep the demo loop on the governance repo.
            print(f"[github] cannot create {repo}, using {self.repo}: {exc}", file=sys.stderr)
            self._refused.add(repo)
            repo = self.repo
        self._ensure_base(repo)
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
        repo = self.repo_for(branch)
        number = None
        try:
            number = self.open_pull_request(branch, message)
        except RuntimeError as exc:
            if not self._already_there(exc):
                raise
            pulls = self._api(
                "GET", f"/pulls?head={self.owner}:{branch}&state=open", repo=repo
            )
            if pulls:
                number = int(pulls[0]["number"])
        if number is None:
            return sha
        try:
            self.merge_pull_request(number, message, repo=repo)
        except RuntimeError as exc:
            if "409" not in str(exc) and "not mergeable" not in str(exc).lower():
                raise
        return sha

    def create_branch(self, branch: str, *, base: str | None = None) -> str:
        repo = self.repo_for(branch)
        ref = self._api("GET", f"/git/ref/heads/{base or self._base_branch(repo)}", repo=repo)
        sha = ref["object"]["sha"]
        self._api("POST", "/git/refs", {"ref": f"refs/heads/{branch}", "sha": sha}, repo=repo)
        return str(sha)

    def _put_file(self, repo: str, branch: str, path: str, content: str, message: str) -> str:
        existing = None
        try:
            existing = self._api("GET", f"/contents/{path}?ref={branch}", repo=repo)
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
        result = self._api("PUT", f"/contents/{path}", payload, repo=repo)
        return str(result["commit"]["sha"])

    def commit_remote_file(
        self, branch: str, path: str, content: str, message: str
    ) -> str:
        return self._put_file(self.repo_for(branch), branch, path, content, message)

    def open_pull_request(self, branch: str, title: str, body: str = "") -> int:
        repo = self.repo_for(branch)
        result = self._api(
            "POST",
            "/pulls",
            {"head": branch, "base": self._base_branch(repo), "title": title, "body": body},
            repo=repo,
        )
        return int(result["number"])

    def merge_pull_request(self, number: int, message: str = "", *, repo: str | None = None) -> str:
        result = self._api(
            "PUT", f"/pulls/{number}/merge", {"commit_message": message}, repo=repo
        )
        if not result.get("merged"):
            raise RuntimeError(str(result.get("message") or "GitHub refused merge"))
        return str(result["sha"])

    def _requirement_repos(self) -> list[str]:
        if not self.per_requirement:
            return []
        prefix = f"{self.repo}-req-"
        found = set(r for r in self._known_repos if r.startswith(prefix))
        try:
            me = self._call("GET", f"{self.api_url}/user")
            listing = (
                f"{self.api_url}/user/repos?per_page=100&affiliation=owner"
                if str(me.get("login") or "").lower() == self.owner.lower()
                else f"{self.api_url}/orgs/{self.owner}/repos?per_page=100"
            )
            for row in self._call("GET", listing) or []:
                name = str(row.get("name") or "")
                if name.startswith(prefix):
                    found.add(name)
        except RuntimeError:
            pass
        return sorted(found)

    def list_open_pull_requests(self) -> list:
        pulls = list(self._api("GET", "/pulls?state=open&per_page=50") or [])
        for repo in self._requirement_repos():
            try:
                pulls += self._api("GET", "/pulls?state=open&per_page=50", repo=repo) or []
            except RuntimeError:
                continue
        return pulls

    def list_reviews(self, number: int, *, repo: str | None = None) -> list:
        return self._api("GET", f"/pulls/{int(number)}/reviews", repo=repo) or []
