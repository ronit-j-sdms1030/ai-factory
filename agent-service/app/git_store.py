"""Git as the artefact store.

Every agent writes its output to a branch and every approval gate becomes a
pull request review, so Git supplies artefact versioning, approver identity,
timestamps, immutable history and rollback without bespoke code. See
``docs/architecture.md`` §6.

This module owns the local repository half — laying out files, committing to a
branch, pushing. Opening pull requests and reading review decisions is the
GitHub API's job and lives in ``github_api.py``, so this half is fully
testable against a throwaway local repository with no credentials and no
network.

Layout, one folder per requirement inside a governance monorepo::

    requirements/<artifact_id>/
        requirement.md
        brd/brd.json
        brd/diagrams/*.mmd
        ui/screens/*.jsx
        workitems/graph.json
        workitems/<dept>/package.json
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from git import Actor, Repo

# Agents commit as themselves so history shows which stage produced what,
# rather than attributing everything to whichever human happened to trigger it.
AGENT_ACTORS = {
    "intake": Actor("intake-agent[bot]", "intake-agent@stark.local"),
    "brd": Actor("brd-agent[bot]", "brd-agent@stark.local"),
    "ui": Actor("ui-agent[bot]", "ui-agent@stark.local"),
    "decomposition": Actor("decomposition-agent[bot]", "decomposition-agent@stark.local"),
}

_SAFE_SEGMENT = re.compile(r"[^A-Za-z0-9._-]+")


class GitStoreError(RuntimeError):
    pass


def _slug(value: str) -> str:
    """Reduce a department or title to something safe in a path or branch name."""
    cleaned = _SAFE_SEGMENT.sub("-", (value or "").strip()).strip("-.").lower()
    return cleaned or "unnamed"


@dataclass
class CommitResult:
    branch: str
    sha: str
    files: list[str]


class GitStore:
    """A local checkout of the governance monorepo."""

    def __init__(self, path: str | Path, *, default_branch: str = "main"):
        self.path = Path(path)
        self.default_branch = default_branch
        if not (self.path / ".git").exists():
            raise GitStoreError(f"{self.path} is not a git repository")
        self.repo = Repo(self.path)

    # ── construction ─────────────────────────────────────────────────────────
    @classmethod
    def init(cls, path: str | Path, *, default_branch: str = "main") -> GitStore:
        """Create an empty repository with one commit, so a branch exists to base others on."""
        path = Path(path)
        path.mkdir(parents=True, exist_ok=True)
        repo = Repo.init(path, initial_branch=default_branch)
        keep = path / ".gitkeep"
        keep.write_text("")
        repo.index.add([str(keep.relative_to(path))])
        repo.index.commit("Initialise governance repository")
        return cls(path, default_branch=default_branch)

    def ensure_base_commit(self) -> None:
        """Guarantee the default branch exists with at least one commit.

        A repository freshly created on GitHub and cloned has no commits at
        all, so there is nothing to branch from. Rather than requiring someone
        to remember to initialise it with a README first, create the base
        commit here — the failure mode otherwise is an opaque KeyError on the
        first artefact write.
        """
        if self.default_branch in self.repo.heads:
            return

        if self.repo.head.is_valid():
            # Commits exist under a different branch name (for example the
            # remote defaults to master). Point the expected name at them
            # rather than starting a parallel history.
            self.repo.create_head(self.default_branch, self.repo.head.commit)
            return

        readme = self.path / "README.md"
        if not readme.exists():
            readme.write_text(
                "# Governance repository\n\n"
                "Requirement artefacts written by the AI Software Factory's agents.\n"
                "Each approval gate is a pull request; see `docs/architecture.md` in the platform repo.\n",
                encoding="utf-8",
            )
        self.repo.index.add(["README.md"])
        self.repo.index.commit("Initialise governance repository")
        if self.repo.active_branch.name != self.default_branch:
            self.repo.active_branch.rename(self.default_branch)

    # ── paths ────────────────────────────────────────────────────────────────
    def requirement_dir(self, artifact_id: str) -> Path:
        return self.path / "requirements" / _slug(artifact_id)

    def branch_name(self, artifact_id: str, stage: str) -> str:
        return f"req/{_slug(artifact_id)}/{_slug(stage)}"

    # ── artefact layouts ─────────────────────────────────────────────────────
    def requirement_files(self, requirement: dict[str, Any], transcript: str) -> dict[str, str]:
        """The intake transcript and structured summary as one reviewable document.

        Markdown rather than JSON because this is the artefact a human actually
        reads at the first gate; the structured form is embedded for machines.
        """
        # Both spellings are accepted because both pipelines write to the same
        # collection during the migration: the Python schema uses snake_case,
        # while records created by the Express backend use camelCase. Reading
        # only one silently produced empty sections in the very document a
        # reviewer reads at the first gate.
        def field(*names: str) -> list:
            for name in names:
                value = requirement.get(name)
                if value:
                    return value
            return []

        body = [
            f"# {requirement.get('title', 'Untitled requirement')}",
            "",
            requirement.get("summary", ""),
            "",
            "## In scope",
            *(f"- {x}" for x in field("in_scope", "inScope")),
            "",
            "## Out of scope",
            *(f"- {x}" for x in field("out_of_scope", "outOfScope")),
            "",
            "## Open questions",
            *(f"- {x}" for x in field("open_questions", "openQuestions")),
            "",
            "## Structured requirement",
            "",
            "```json",
            json.dumps(requirement, indent=2, default=str),
            "```",
            "",
            "## Intake transcript",
            "",
            transcript,
            "",
        ]
        return {"requirement.md": "\n".join(body)}

    def brd_files(self, brd: dict[str, Any]) -> dict[str, str]:
        """The BRD, with diagrams split out so CI can lint them independently."""
        files = {"brd/brd.json": json.dumps(brd, indent=2, default=str)}
        if brd.get("architecture_diagram"):
            files["brd/diagrams/architecture.mmd"] = brd["architecture_diagram"]
        if brd.get("db_schema_diagram"):
            files["brd/diagrams/schema.mmd"] = brd["db_schema_diagram"]
        return files

    def ui_files(self, ui: dict[str, Any]) -> dict[str, str]:
        files: dict[str, str] = {}
        for screen in ui.get("screens") or []:
            files[f"ui/screens/{_slug(screen.get('name', 'screen'))}.jsx"] = screen.get("source", "")
        if ui.get("clarifications"):
            files["ui/clarifications.md"] = "\n".join(f"- {c}" for c in ui["clarifications"])
        return files

    def workitem_files(self, decomposition: dict[str, Any]) -> dict[str, str]:
        """The dependency graph and per-department packages as separate files.

        Separate because they are approved by different people: the graph by
        VP, each package by its owning team lead. One file per reviewer keeps
        those gates independent.
        """
        files = {
            "workitems/graph.json": json.dumps(
                {
                    "work_items": decomposition.get("work_items") or [],
                    "integrity": decomposition.get("integrity") or {},
                },
                indent=2,
                default=str,
            )
        }
        for package in decomposition.get("packages") or []:
            files[f"workitems/{_slug(package.get('team', 'unassigned'))}/package.json"] = json.dumps(
                package, indent=2, default=str
            )
        return files

    # ── writing ──────────────────────────────────────────────────────────────
    def commit_artefacts(
        self,
        *,
        artifact_id: str,
        stage: str,
        files: dict[str, str],
        message: str,
        agent: str,
    ) -> CommitResult:
        """Write ``files`` on a fresh branch off the default branch and commit.

        Branching from the default branch each time, rather than from whatever
        happens to be checked out, keeps each gate's pull request reviewable in
        isolation — a reviewer sees only that stage's change, not everything
        since.
        """
        if not files:
            raise GitStoreError("refusing to commit an empty changeset")

        actor = AGENT_ACTORS.get(agent)
        if actor is None:
            raise GitStoreError(f"unknown agent '{agent}'")

        self.ensure_base_commit()

        branch = self.branch_name(artifact_id, stage)
        base = self.repo.heads[self.default_branch]
        head = self.repo.create_head(branch, base) if branch not in self.repo.heads else self.repo.heads[branch]
        head.checkout()

        root = self.requirement_dir(artifact_id)
        root.mkdir(parents=True, exist_ok=True)
        root_resolved = root.resolve()

        written: list[str] = []
        for relative, content in files.items():
            target = root / relative
            # Scoped to this requirement's folder, not merely to the
            # repository. Paths originate in model output, and a check against
            # the repository root would still permit "../../other-artifact",
            # letting one requirement's agents overwrite another's approved
            # artefacts while staying technically inside the repo.
            if not target.resolve().is_relative_to(root_resolved):
                raise GitStoreError(f"refusing to write outside the requirement folder: {relative}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content if isinstance(content, str) else str(content), encoding="utf-8")
            written.append(str(target.relative_to(self.path)))

        self.repo.index.add(written)
        commit = self.repo.index.commit(message, author=actor, committer=actor)
        return CommitResult(branch=branch, sha=commit.hexsha, files=sorted(written))

    def push(self, branch: str, *, remote: str = "origin") -> None:
        if remote not in [r.name for r in self.repo.remotes]:
            raise GitStoreError(f"no remote named '{remote}'")
        self.repo.remote(remote).push(refspec=f"{branch}:{branch}", set_upstream=True)

    def read(self, artifact_id: str, relative: str, *, ref: str | None = None) -> str | None:
        """Read an artefact at a ref, or None if it does not exist there."""
        path = f"requirements/{_slug(artifact_id)}/{relative}"
        try:
            blob = (self.repo.commit(ref) if ref else self.repo.head.commit).tree / path
            return blob.data_stream.read().decode("utf-8")
        except (KeyError, ValueError):
            return None


def open_store() -> GitStore | None:
    """The configured store, or None when Git integration is not set up.

    Returns None rather than raising so the pipeline keeps working without Git
    — the migration should not require every deployment to have a repository
    configured before anything runs.
    """
    path = os.environ.get("GOVERNANCE_REPO_PATH")
    if not path:
        return None
    return GitStore(path)
