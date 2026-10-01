"""Local Git and GitHub App repository adapters."""

from __future__ import annotations

import base64
import json
import re
import sys
from urllib import error, request

from phase1.catalog import github_repo_name
from phase1.gitrepo import GovernanceRepo

_REQ_ID = re.compile(r"REQ-\d+", re.I)


class LocalGitRepository(GovernanceRepo):
    def delete_branch(self, branch: str) -> None:
        """Drop a local branch. Main is never deleted."""
        if not branch or branch in {"main", "master"}:
            return
        current = self._run(["git", "rev-parse", "--abbrev-ref", "HEAD"]).strip()
        if current == branch:
            self._run(["git", "checkout", "main"])
        if self._run(["git", "branch", "--list", branch]).strip():
            self._run(["git", "branch", "-D", branch])

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
    repo named after the product: ``travel-company-req-0001``. Until a title
    exists the fallback is ``<repo>-req-nnnn``. ``repo`` is then only a name
    prefix: branches without a requirement id (skills, settings, main) and
    requirements whose repo cannot be created stay in the local tree, so the
    shared governance repo never needs to exist on GitHub.
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
        self._titles: dict[str, str] = {}

    def note_title(self, requirement_id: str, title: str) -> None:
        """Remember the product name so the GitHub repo is not only the id."""
        clean = str(title or "").strip()
        found = _REQ_ID.search(requirement_id or "")
        if clean and found:
            self._titles[found.group(0).upper()] = clean

    def repo_for(self, branch: str) -> str:
        if not self.per_requirement:
            return self.repo
        found = _REQ_ID.search(branch or "")
        if not found:
            return self.repo
        rid = found.group(0).upper()
        name = github_repo_name(
            rid, self._titles.get(rid, ""), base=self.repo, per_requirement=True
        )
        return self.repo if name in self._refused else name

    def _local_only(self, repo: str) -> bool:
        return self.per_requirement and repo == self.repo

    def _legacy_repo(self, requirement_id: str) -> str:
        found = _REQ_ID.search(requirement_id or "")
        if not found:
            return ""
        return f"{self.repo}-{found.group(0).lower()}"

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
                raw = response.read()
            if not raw.strip():
                return {}
            return json.loads(raw)
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
        found = _REQ_ID.search(repo or "")
        requirement = found.group(0).upper() if found else repo.upper()
        title = self._titles.get(requirement, "")
        description = (
            f"{title} ({requirement})" if title else f"Requirement {requirement}"
        )
        description_text = f"{description} — governed product artefacts"
        try:
            self._api("GET", "", repo=repo)
            if title:
                try:
                    self._api(
                        "PATCH", "", {"name": repo, "description": description_text}, repo=repo
                    )
                except RuntimeError:
                    pass
        except RuntimeError as exc:
            if ": 404 " not in str(exc):
                raise
            renamed = False
            for candidate in self._same_requirement_repos(requirement, repo):
                try:
                    info = self._api("GET", "", repo=candidate)
                except RuntimeError as found_exc:
                    if ": 404 " not in str(found_exc):
                        raise
                    continue
                if not isinstance(info, dict) or not info.get("name"):
                    continue
                current = str(info.get("name") or candidate)
                if current == repo:
                    renamed = True
                    break
                self._api(
                    "PATCH",
                    "",
                    {"name": repo, "description": description_text},
                    repo=current,
                )
                renamed = True
                break
            if not renamed:
                me = self._call("GET", f"{self.api_url}/user")
                create = (
                    f"{self.api_url}/user/repos"
                    if str(me.get("login") or "").lower() == self.owner.lower()
                    else f"{self.api_url}/orgs/{self.owner}/repos"
                )
                try:
                    self._call(
                        "POST",
                        create,
                        {
                            "name": repo,
                            "private": True,
                            "auto_init": True,
                            "description": description_text,
                            "homepage": f"http://127.0.0.1:5173/preview/{requirement}",
                        },
                    )
                except RuntimeError as create_exc:
                    if not self._already_there(create_exc):
                        raise
        self._known_repos.add(repo)

    def _same_requirement_repos(self, requirement: str, desired: str) -> list[str]:
        """Repos already opened for this id, under an older name."""
        suffix = "-" + requirement.lower()
        names: list[str] = []
        for candidate in (
            self._legacy_repo(requirement),
            f"requirement-{requirement.lower()}",
        ):
            if candidate and candidate != desired and candidate not in names:
                names.append(candidate)
        try:
            listed = self._requirement_repos()
        except Exception:
            listed = []
        for name in listed:
            if name != desired and name.lower().endswith(suffix) and name not in names:
                names.append(name)
        return names

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
        delete: list[str] | None = None,
    ) -> str:
        sha = super().commit_files(
            branch, files, message, author=author, email=email, delete=delete
        )
        repo = self.repo_for(branch)
        if self._local_only(repo):
            self._publish_root_readme(branch, files)
            return sha
        try:
            self._ensure_repo(repo)
        except RuntimeError as exc:
            if not self.per_requirement:
                raise
            # Token cannot create repos: keep the demo loop on the local tree.
            print(f"[github] cannot create {repo}, {branch} stays local: {exc}", file=sys.stderr)
            self._refused.add(repo)
            return sha
        self._ensure_base(repo)
        if branch != "main":
            try:
                self.create_branch(branch)
            except RuntimeError as exc:
                if not self._already_there(exc):
                    raise
        for path, content in files.items():
            self.commit_remote_file(branch, path, content, message)
        self._publish_root_readme(branch, files)
        return sha

    def merge_to_main(self, branch: str, message: str) -> str:
        sha = super().merge_to_main(branch, message)
        if branch == "main":
            return sha
        repo = self.repo_for(branch)
        if self._local_only(repo):
            return sha
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
        if number is not None:
            try:
                self.merge_pull_request(number, message, repo=repo)
            except RuntimeError as exc:
                text = str(exc).lower()
                # GitHub returns 405/409 when the PR is dirty or already merged.
                # Local merge already landed; do not fail gate clearance for that.
                if (
                    "409" not in str(exc)
                    and "405" not in str(exc)
                    and "not mergeable" not in text
                    and "merge conflict" not in text
                ):
                    raise
        self._publish_root_readme(branch, {})
        return sha

    def _readme_ids(self, branch: str, files: dict[str, str]) -> list[str]:
        """Requirement ids named by the branch or by paths in the commit."""
        ids: list[str] = []
        found = _REQ_ID.search(branch or "")
        if found:
            ids.append(found.group(0).upper())
        for path in files:
            match = re.search(r"requirements/(REQ-\d+)/", str(path), re.I)
            if match:
                rid = match.group(1).upper()
                if rid not in ids:
                    ids.append(rid)
        return ids

    def _publish_root_readme(self, branch: str, files: dict[str, str]) -> None:
        """The GitHub repo home page is README.md, not a nested path."""
        if not self.per_requirement:
            return
        for rid in self._readme_ids(branch, files):
            content = files.get(f"requirements/{rid}/README.md", "")
            if not content:
                path = self.root / f"requirements/{rid}/README.md"
                if path.is_file():
                    content = path.read_text(encoding="utf-8")
            if not content:
                continue
            repo = self.repo_for(f"scope/{rid}")
            if repo == self.repo or repo in self._refused:
                continue
            try:
                self._ensure_repo(repo)
                self._ensure_base(repo)
                if self._readme_is_current(repo, content):
                    continue
                self._put_file(repo, "main", "README.md", content, "Update requirement status")
            except RuntimeError as exc:
                print(f"[github] README for {repo} was not updated: {exc}", file=sys.stderr)

    def _readme_is_current(self, repo: str, content: str) -> bool:
        try:
            existing = self._api("GET", "/contents/README.md?ref=main", repo=repo)
        except RuntimeError:
            return False
        raw = ""
        if isinstance(existing, dict):
            raw = str(existing.get("content") or "")
        if not raw:
            return False
        try:
            current = base64.b64decode(raw).decode()
        except (ValueError, UnicodeError):
            return False
        return current.strip() == content.strip()

    def delete_branch(self, branch: str) -> None:
        super().delete_branch(branch)
        if not branch or branch in {"main", "master"}:
            return
        repo = self.repo_for(branch)
        if self._local_only(repo):
            return
        try:
            self._api("DELETE", f"/git/refs/heads/{branch}", repo=repo)
        except RuntimeError as exc:
            if ": 404 " not in str(exc) and "422" not in str(exc):
                print(f"[github] {branch} was not deleted on {repo}: {exc}", file=sys.stderr)

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
        named = re.compile(r"-req-\d+$", re.I)
        found = set(r for r in self._known_repos if r.startswith(prefix) or named.search(r))
        try:
            me = self._call("GET", f"{self.api_url}/user")
            listing = (
                f"{self.api_url}/user/repos?per_page=100&affiliation=owner"
                if str(me.get("login") or "").lower() == self.owner.lower()
                else f"{self.api_url}/orgs/{self.owner}/repos?per_page=100"
            )
            rows = self._call("GET", listing) or []
            if not isinstance(rows, list):
                rows = []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                name = str(row.get("name") or "")
                desc = str(row.get("description") or "").lower()
                if name.startswith(prefix) or (named.search(name) and "governed" in desc):
                    found.add(name)
        except RuntimeError:
            pass
        return sorted(found)

    def list_open_pull_requests(self) -> list:
        pulls = (
            [] if self.per_requirement
            else list(self._api("GET", "/pulls?state=open&per_page=50") or [])
        )
        for repo in self._requirement_repos():
            try:
                pulls += self._api("GET", "/pulls?state=open&per_page=50", repo=repo) or []
            except RuntimeError:
                continue
        return pulls

    def list_reviews(self, number: int, *, repo: str | None = None) -> list:
        return self._api("GET", f"/pulls/{int(number)}/reviews", repo=repo) or []
