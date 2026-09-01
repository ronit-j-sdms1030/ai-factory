"""Publishing artefacts to Git and opening the pull request that gates them.

The join between the agents and the two Git modules: ``git_store`` writes and
commits locally, ``github_api`` pushes and opens the pull request.

Every function here is **best-effort**. A Git or GitHub failure returns an
error string rather than raising, because the artefact itself is already saved
in MongoDB and the approval flow must not stop because a network call failed.
Losing an approval — or blocking one — over a transient GitHub outage would be
far worse than a missing pull request someone can republish.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any, Callable

from . import github_api, reviewers
from .git_store import GitStore, GitStoreError, _slug, open_store

# How much of the title goes into the folder name. Long enough to identify the
# requirement at a glance in a branch list, short enough to stay readable.
_TITLE_SEGMENT = 44


def repo_folder(artifact: dict[str, Any]) -> str:
    """The folder and branch segment for one requirement.

    A raw Mongo id tells a reviewer nothing — ``req/6a968da2ee951e14aa09ae1a/brd``
    is indistinguishable from every other branch at a glance. This pairs the
    title with a short id suffix, so branches read as
    ``req/starklogix-warehouse-and-logistics-6a968da2/brd`` and still cannot
    collide between two requirements sharing a title.

    Computed once and stored on the artifact, never recomputed: the FSD edit
    chat can rename a requirement mid-pipeline, and a folder that moved would
    strand every artefact already committed under the old name in a branch
    nobody looks at again.
    """
    existing = artifact.get("repoSlug")
    if existing:
        return existing

    short = str(artifact.get("_id"))[-8:]
    title = _slug(artifact.get("title") or "")[:_TITLE_SEGMENT].strip("-")
    folder = f"{title}-{short}" if title and title != "unnamed" else f"requirement-{short}"
    artifact["repoSlug"] = folder
    return folder

log = logging.getLogger(__name__)

# Which agent authors each stage, and which tier reviews the resulting PR.
STAGES: dict[str, dict[str, Any]] = {
    "requirement": {"agent": "intake", "title": "Requirement", "reviewers": ("tier-md", "tier-ceo")},
    "brd": {"agent": "brd", "title": "BRD", "reviewers": ("tier-md", "tier-ceo", "tier-vp")},
    "ui": {"agent": "ui", "title": "UI", "reviewers": ("tier-vp",)},
    "workitems": {"agent": "decomposition", "title": "Work items", "reviewers": ("tier-vp",)},
}


@dataclass
class PublishResult:
    branch: str | None = None
    sha: str | None = None
    pull_request_url: str | None = None
    pull_request_number: int | None = None
    pushed: bool = False
    reviewers_requested: list[str] | None = None
    unmapped_reviewers: list[str] | None = None
    error: str | None = None


def _pr_body(stage: str, artifact: dict[str, Any], extra: str = "") -> str:
    stage_title = STAGES[stage]["title"]
    return "\n".join(
        [
            f"Automated {stage_title.lower()} for **{artifact.get('title', 'Untitled')}**.",
            "",
            f"- Artifact: `{artifact.get('_id')}`",
            f"- Stage: `{stage}`",
            f"- Originator tier: `{(artifact.get('originator') or {}).get('tierId') or 'client'}`",
            "",
            extra,
            "",
            "---",
            "Approving this pull request advances the requirement past its gate. "
            "Requesting changes returns it to the agent that produced it.",
        ]
    ).strip()


# One repository, one working tree, a branch per requirement — so publishing
# is not safe to do twice at once. Generation runs on background threads now,
# and two of them reaching this point together would have one checking out its
# branch while the other was staging a commit, producing a commit against the
# wrong branch or a hard failure on the index lock. Model calls are the slow
# part and happen outside this lock; the git work it serialises is seconds.
_PUBLISH_LOCK = threading.Lock()


def publish(
    *,
    artifact: dict[str, Any],
    stage: str,
    build_files: Callable[[GitStore], dict[str, str]],
    body_extra: str = "",
) -> PublishResult:
    """Commit a stage's artefacts, push, and open its pull request.

    ``build_files`` is a callable rather than a dict so the caller can use the
    store's own layout helpers without this module needing to know the shape
    of every artefact.

    Serialised process-wide: see ``_PUBLISH_LOCK``.
    """
    if stage not in STAGES:
        return PublishResult(error=f"unknown stage '{stage}'")

    with _PUBLISH_LOCK:
        return _publish_locked(artifact=artifact, stage=stage, build_files=build_files, body_extra=body_extra)


def _publish_locked(
    *,
    artifact: dict[str, Any],
    stage: str,
    build_files: Callable[[GitStore], dict[str, str]],
    body_extra: str = "",
) -> PublishResult:
    store = open_store()
    if store is None:
        return PublishResult()  # Git not configured — silently inert, by design

    artifact_id = repo_folder(artifact)
    spec = STAGES[stage]

    try:
        files = build_files(store)
        if not files:
            return PublishResult(error="nothing to publish for this stage")
        commit = store.commit_artefacts(
            artifact_id=artifact_id,
            stage=stage,
            agent=spec["agent"],
            files=files,
            message=f"{spec['title']}: {artifact.get('title', 'Untitled')}",
        )
    except (GitStoreError, OSError) as exc:
        log.exception("git commit failed for %s/%s", artifact_id, stage)
        return PublishResult(error=f"git commit failed: {exc}")

    result = PublishResult(branch=commit.branch, sha=commit.sha)

    config = github_api.load_config()
    if config is None:
        return result  # committed locally; no remote configured

    try:
        _push_with_token(store, commit.branch, config)
        result.pushed = True
    except Exception as exc:  # noqa: BLE001 — surfaced, never fatal
        log.exception("push failed for %s", commit.branch)
        result.error = f"push failed: {exc}"
        return result

    try:
        client = github_api.GitHubClient(config)
        pull = client.open_pull_request(
            branch=commit.branch,
            title=f"{spec['title']}: {artifact.get('title', 'Untitled')}",
            body=_pr_body(stage, artifact, body_extra),
            base=store.default_branch,
        )
        result.pull_request_url = pull.get("html_url")
        result.pull_request_number = pull.get("number")
        _assign_reviewers(client, pull, stage, result)
    except Exception as exc:  # noqa: BLE001
        log.exception("opening the pull request failed for %s", commit.branch)
        result.error = f"pull request failed: {exc}"

    return result


def _push_with_token(store: GitStore, branch: str, config: github_api.GitHubConfig) -> None:
    """Push the branch, and the base branch it will target, over HTTPS.

    The token is injected into the remote URL for the duration of the call and
    never written to ``.git/config``, so it cannot end up committed or left
    readable in the working tree.

    The base branch is pushed first because a repository created empty on
    GitHub has no branches at all: the base commit exists only in the local
    clone, so opening a pull request against it fails with "base invalid" even
    though the feature branch pushed fine.
    """
    token = github_api.access_token(config)
    url = f"https://x-access-token:{token}@github.com/{config.repo}.git"

    base = store.default_branch
    if base in store.repo.heads:
        # Not forced: the base branch is shared, and clobbering someone else's
        # commits there would be far worse than failing this push.
        store.repo.git.push(url, f"{base}:{base}")

    # Pushing to a URL rather than a named remote means git has no
    # remote-tracking ref, so a bare --force-with-lease cannot evaluate its
    # lease and refuses with "stale info". Look the remote SHA up explicitly
    # and pin the lease to it: that keeps the protection (a concurrent push
    # between this lookup and the push still aborts) instead of downgrading to
    # an unconditional --force, which would silently discard someone's work.
    remote_sha = ""
    try:
        listing = store.repo.git.ls_remote(url, branch)
        if listing.strip():
            remote_sha = listing.split()[0]
    except Exception:  # noqa: BLE001 — treated as "branch does not exist yet"
        pass

    if remote_sha:
        store.repo.git.push(url, f"{branch}:{branch}", f"--force-with-lease={branch}:{remote_sha}")
    else:
        # First push of this branch; nothing to overwrite, so no lease needed.
        store.repo.git.push(url, f"{branch}:{branch}")


def _assign_reviewers(client, pull: dict, stage: str, result: PublishResult) -> None:
    """Put the right people on the pull request, by team or by name.

    Teams are tried first because that is the mechanism CODEOWNERS and branch
    protection enforce. They only exist inside a GitHub organisation, so on a
    personal account the call fails and individual users are requested
    instead — the same humans, without repository-level enforcement.

    Never fatal: an unreviewed pull request is still a record of the gate, and
    failing the publish here would lose the artefact entirely.
    """
    number = pull.get("number")
    author = ((pull.get("user") or {}).get("login"))

    try:
        client.request_reviewers(number, teams=reviewers.team_slugs(stage))
        result.reviewers_requested = reviewers.team_slugs(stage)
        return
    except github_api.GitHubError as exc:
        log.info("team reviewers unavailable for %s (likely a personal account): %s", stage, exc)

    logins = reviewers.github_logins_for(stage, exclude_login=author)
    unmapped = reviewers.unmapped_reviewers(stage)
    result.unmapped_reviewers = unmapped or None

    if not logins:
        # Nobody is assignable. Say so loudly — a gate with no reviewer looks
        # healthy and waits forever.
        result.error = (
            f"no reviewer could be assigned for '{stage}': no GitHub team, and no "
            f"approver has a githubLogin mapped ({', '.join(unmapped) or 'no candidates'})"
        )
        return

    try:
        client.request_reviewers(number, users=logins)
        result.reviewers_requested = logins
    except github_api.GitHubError as exc:
        log.warning("could not request individual reviewers for %s: %s", stage, exc)
        result.error = f"reviewers could not be requested: {exc}"
