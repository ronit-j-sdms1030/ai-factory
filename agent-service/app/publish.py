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
from dataclasses import dataclass
from typing import Any, Callable

from . import github_api
from .git_store import GitStore, GitStoreError, open_store

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
    pushed: bool = False
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
    """
    if stage not in STAGES:
        return PublishResult(error=f"unknown stage '{stage}'")

    store = open_store()
    if store is None:
        return PublishResult()  # Git not configured — silently inert, by design

    artifact_id = str(artifact.get("_id"))
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

        # Requesting reviewers is a nice-to-have: on a personal account the
        # tier teams do not exist, and the pull request is still a valid gate
        # without them. Never fail the publish over it.
        try:
            client.request_reviewers(pull["number"], teams=list(spec["reviewers"]))
        except github_api.GitHubError as exc:
            log.info("could not request reviewers (teams may not exist): %s", exc)
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
