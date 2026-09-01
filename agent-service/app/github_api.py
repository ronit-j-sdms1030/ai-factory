"""GitHub API — pull requests as approval gates.

The second half of the Git integration. ``git_store`` writes artefacts to a
branch; this opens the pull request that gates them, reads the review decision,
and merges on approval.

Authentication prefers a **GitHub App** over a personal access token. An App
gets webhooks, per-repository permissions, and commits attributed to the app
rather than to whichever human's credentials were used — all three matter for
an audit trail that has to survive someone leaving. A token is accepted as a
development fallback.

Sequencing note: GitHub reviews are unordered, but approval chains are not.
GitHub therefore enforces *who may approve* (via CODEOWNERS and branch
protection) while the orchestrator enforces *what order* — it requests
reviewers only for the current gate and ignores an approval arriving early
from a later gate's tier. See ``docs/architecture.md`` §6.4.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from dataclasses import dataclass
from typing import Any

import httpx

API_ROOT = "https://api.github.com"
_ACCEPT = "application/vnd.github+json"


class GitHubError(RuntimeError):
    pass


class GitHubNotConfigured(GitHubError):
    """Raised when a GitHub operation is attempted without credentials."""


# ── configuration ────────────────────────────────────────────────────────────
@dataclass(frozen=True)
class GitHubConfig:
    repo: str                      # "owner/name"
    token: str | None = None       # PAT or installation token
    app_id: str | None = None
    private_key: str | None = None
    installation_id: str | None = None
    webhook_secret: str | None = None

    @property
    def owner(self) -> str:
        return self.repo.split("/", 1)[0]


def load_config() -> GitHubConfig | None:
    """Configuration from the environment, or None when GitHub is not set up.

    Returns None rather than raising so the pipeline keeps running without
    GitHub — the integration must be adoptable incrementally.
    """
    repo = os.environ.get("GITHUB_REPO")
    if not repo:
        return None
    return GitHubConfig(
        repo=repo,
        token=os.environ.get("GITHUB_TOKEN"),
        app_id=os.environ.get("GITHUB_APP_ID"),
        private_key=os.environ.get("GITHUB_APP_PRIVATE_KEY"),
        installation_id=os.environ.get("GITHUB_APP_INSTALLATION_ID"),
        webhook_secret=os.environ.get("GITHUB_WEBHOOK_SECRET"),
    )


# ── authentication ───────────────────────────────────────────────────────────
def _app_jwt(config: GitHubConfig) -> str:
    import jwt  # imported lazily so the module loads without the crypto extra

    now = int(time.time())
    return jwt.encode(
        # Backdated 60s because GitHub rejects a JWT whose iat is in the
        # future, and small clock skew between hosts is normal.
        {"iat": now - 60, "exp": now + 540, "iss": config.app_id},
        config.private_key,
        algorithm="RS256",
    )


def access_token(config: GitHubConfig) -> str:
    """An installation token for the App, or the configured token."""
    if config.app_id and config.private_key and config.installation_id:
        response = httpx.post(
            f"{API_ROOT}/app/installations/{config.installation_id}/access_tokens",
            headers={"Authorization": f"Bearer {_app_jwt(config)}", "Accept": _ACCEPT},
            timeout=30,
        )
        if response.status_code >= 300:
            raise GitHubError(f"could not mint an installation token: {response.status_code} {response.text}")
        return response.json()["token"]

    if config.token:
        return config.token

    raise GitHubNotConfigured("no GitHub App credentials and no GITHUB_TOKEN")


# ── client ───────────────────────────────────────────────────────────────────
class GitHubClient:
    def __init__(self, config: GitHubConfig):
        self.config = config
        self._token: str | None = None

    def _headers(self) -> dict[str, str]:
        if self._token is None:
            self._token = access_token(self.config)
        return {"Authorization": f"Bearer {self._token}", "Accept": _ACCEPT}

    def _request(self, method: str, path: str, **kwargs) -> Any:
        response = httpx.request(
            method, f"{API_ROOT}{path}", headers=self._headers(), timeout=30, **kwargs
        )
        if response.status_code >= 300:
            raise GitHubError(f"{method} {path} -> {response.status_code}: {response.text}")
        return response.json() if response.content else None

    # ── pull requests ────────────────────────────────────────────────────────
    def open_pull_request(self, *, branch: str, title: str, body: str, base: str = "main") -> dict:
        """Open the pull request for a branch, or return the one already open.

        Republishing a stage — after a revision, or a retry following a failed
        push — must be idempotent. GitHub rejects a duplicate with a 422, which
        is not an error condition here: the gate already exists and the branch
        has just been updated to point at the new commit.
        """
        try:
            return self._request(
                "POST", f"/repos/{self.config.repo}/pulls",
                json={"title": title, "head": branch, "base": base, "body": body},
            )
        except GitHubError as exc:
            if "already exists" not in str(exc):
                raise
            existing = self.pull_request_for(branch)
            if existing is None:
                raise
            return existing

    def pull_request_for(self, branch: str) -> dict | None:
        """The open pull request whose head is ``branch``, if there is one."""
        found = self._request(
            "GET", f"/repos/{self.config.repo}/pulls",
            params={"state": "open", "head": f"{self.config.owner}:{branch}"},
        )
        return found[0] if found else None

    def request_reviewers(self, number: int, *, teams: list[str] | None = None, users: list[str] | None = None) -> dict:
        """Ask the tier that owns the current gate to review.

        Only the current gate's reviewers are requested — that is how the
        orchestrator imposes order on a review system that has none.
        """
        payload: dict[str, list[str]] = {}
        if teams:
            payload["team_reviewers"] = teams
        if users:
            payload["reviewers"] = users
        if not payload:
            raise GitHubError("no reviewers given")
        return self._request(
            "POST", f"/repos/{self.config.repo}/pulls/{number}/requested_reviewers", json=payload
        )

    def reviews(self, number: int) -> list[dict]:
        return self._request("GET", f"/repos/{self.config.repo}/pulls/{number}/reviews") or []

    def merge(self, number: int, *, method: str = "squash") -> dict:
        """Merge is the approval record — the merge commit is the audit entry."""
        return self._request(
            "PUT", f"/repos/{self.config.repo}/pulls/{number}/merge", json={"merge_method": method}
        )

    def open_pull_requests(self) -> list[dict]:
        """Used by reconciliation: a dropped webhook must not strand a thread."""
        return self._request("GET", f"/repos/{self.config.repo}/pulls?state=open") or []


# ── webhooks ─────────────────────────────────────────────────────────────────
def verify_signature(body: bytes, signature: str | None, secret: str | None) -> bool:
    """Validate GitHub's HMAC-SHA256 webhook signature.

    Anyone who can reach the endpoint can POST to it, and the payload decides
    whether an approval gate opens, so an unverified webhook is an
    authorisation bypass. Fails closed when no secret is configured, and
    compares in constant time.
    """
    if not secret or not signature:
        return False
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


@dataclass(frozen=True)
class ReviewEvent:
    """The parts of a pull_request_review webhook the pipeline acts on."""

    action: str            # "approve" | "reject" | "revise"
    reviewer_login: str
    branch: str
    pr_number: int
    artifact_id: str | None
    stage: str | None
    body: str


_REVIEW_STATES = {
    "approved": "approve",
    "changes_requested": "revise",
    "dismissed": "reject",
}


def parse_branch(branch: str) -> tuple[str | None, str | None]:
    """Recover the artifact id and stage from ``req/<artifact_id>/<stage>``."""
    parts = (branch or "").split("/")
    if len(parts) == 3 and parts[0] == "req":
        return parts[1], parts[2]
    return None, None


def parse_review_event(payload: dict) -> ReviewEvent | None:
    """Translate a webhook payload into a gate decision, or None if irrelevant.

    Comment-only reviews are deliberately ignored: a comment is not a
    decision, and treating one as an approval would open a gate nobody
    actually cleared.
    """
    review = payload.get("review") or {}
    pull = payload.get("pull_request") or {}
    action = _REVIEW_STATES.get((review.get("state") or "").lower())
    if action is None:
        return None

    branch = ((pull.get("head") or {}).get("ref")) or ""
    artifact_id, stage = parse_branch(branch)
    return ReviewEvent(
        action=action,
        reviewer_login=((review.get("user") or {}).get("login")) or "",
        branch=branch,
        pr_number=pull.get("number") or 0,
        artifact_id=artifact_id,
        stage=stage,
        body=review.get("body") or "",
    )


# ── CODEOWNERS ───────────────────────────────────────────────────────────────
def codeowners(org: str, departments: list[str]) -> str:
    """Map approval tiers onto GitHub teams, enforced by branch protection.

    This is what satisfies SoW 6.0's "branch protection and required-reviewer
    rules enforced at repository level", and it gives segregation of duties for
    free since GitHub will not let an author approve their own pull request.
    """
    lines = [
        "# Generated — maps approval tiers to GitHub teams.",
        "# Ordering matters: the last matching rule wins.",
        "",
        f"/requirements/*/requirement.md          @{org}/tier-md @{org}/tier-ceo",
        f"/requirements/*/brd/                    @{org}/tier-md @{org}/tier-ceo @{org}/tier-vp",
        f"/requirements/*/ui/                     @{org}/tier-vp",
        f"/requirements/*/workitems/graph.json    @{org}/tier-vp",
    ]
    for dept in departments:
        slug = dept.lower().replace(" & ", "-").replace(" ", "-")
        lines.append(f"/requirements/*/workitems/{slug}/".ljust(40) + f"@{org}/tl-{slug}")
    lines += ["", f"/prompts/                               @{org}/tier-vp", ""]
    return "\n".join(lines)
