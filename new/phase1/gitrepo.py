"""Governance repository — Git holds artefacts; Postgres holds process state.

Layout matches §12. One branch per stage. ``main`` is accepted decisions only.
If Postgres and Git disagree about an artefact, Git wins — this module is that
authority.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import intake_skill
import skill_registry


class GitError(Exception):
    """A git command failed. The artefact was not committed."""


class GovernanceRepo:
    def __init__(self, root: Path):
        self.root = Path(root)

    def init(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        if (self.root / ".git").exists():
            return
        self._run(["git", "init", "-b", "main"])
        self._seed_skills()
        self._run(["git", "add", "-A"])
        self._commit("seed governance skill files", "platform", "platform@local")

    def _seed_skills(self) -> None:
        shipped_root = Path(__file__).resolve().parent.parent / "skills"
        dest = self.root / "skills"
        dest.mkdir(parents=True, exist_ok=True)
        skill = dest / Path(intake_skill.PATH).name
        if not skill.exists():
            skill.write_text(intake_skill.DEFAULT, encoding="utf-8")
        if not shipped_root.is_dir():
            return
        for item in shipped_root.iterdir():
            if item.suffix in {".md", ".json"}:
                target = dest / item.name
                if not target.exists():
                    shutil.copy2(item, target)
            elif item.name == "vendor" and item.is_dir():
                for skill_md in item.glob("*/SKILL.md"):
                    target = dest / "vendor" / skill_md.parent.name / "SKILL.md"
                    if target.exists():
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(skill_md, target)

    def commit_files(
        self,
        branch: str,
        files: dict[str, str],
        message: str,
        *,
        author: str,
        email: str,
    ) -> str:
        listed = self._run(["git", "branch", "--list", branch]).strip()
        if listed:
            self._run(["git", "checkout", branch])
        else:
            self._run(["git", "checkout", "main"])
            self._run(["git", "checkout", "-b", branch])
        for rel, content in files.items():
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            self._run(["git", "add", "--", rel])
        try:
            self._commit(message, author, email)
        except GitError as exc:
            if "nothing to commit" not in str(exc).lower():
                raise
        return self.rev_parse("HEAD")

    def merge_to_main(self, branch: str, message: str) -> str:
        self._run(["git", "checkout", "main"])
        self._run(
            [
                "git",
                "-c",
                "user.name=platform",
                "-c",
                "user.email=platform@local",
                "merge",
                "--no-ff",
                branch,
                "-m",
                message,
            ]
        )
        return self.rev_parse("main")

    def read(self, rel: str, ref: str = "main") -> str:
        return self._run(["git", "show", f"{ref}:{rel}"])

    def exists(self, rel: str, ref: str = "main") -> bool:
        try:
            self.read(rel, ref)
            return True
        except GitError:
            return False

    def rev_parse(self, ref: str) -> str:
        return self._run(["git", "rev-parse", "--short", ref]).strip()

    def skill(self) -> intake_skill.SkillFile:
        path = self.root / intake_skill.PATH
        if not path.exists():
            return intake_skill.shipped()
        try:
            sha = self.rev_parse("main")
            return intake_skill.load_from_repo(self.root, version=sha, edited_by="platform")
        except (OSError, intake_skill.IncompleteSkillFile):
            return intake_skill.shipped()

    def brd_template(self) -> str:
        shipped = Path(__file__).resolve().parent.parent / "skills/brd.template.md"
        path = self.root / "skills/brd.template.md"
        if path.exists():
            text = path.read_text(encoding="utf-8")
            if "{{out_of_scope}}" in text and "{{journeys}}" in text and "{{page_behaviour}}" in text:
                return text
        return shipped.read_text(encoding="utf-8")

    def design_system(self) -> str:
        path = self.root / "skills/design-system.skill.md"
        if path.exists():
            return path.read_text(encoding="utf-8")
        shipped = Path(__file__).resolve().parent.parent / "skills/design-system.skill.md"
        return shipped.read_text(encoding="utf-8") if shipped.exists() else ""

    def bundle(self, agent: str) -> skill_registry.SkillBundle:
        sha = self.rev_parse("main")
        return skill_registry.load_bundle(agent, root=self.root, version=sha)

    def _commit(self, message: str, name: str, email: str) -> None:
        self._run(
            [
                "git",
                "-c",
                f"user.name={name}",
                "-c",
                f"user.email={email}",
                "commit",
                "-m",
                message,
            ]
        )

    def _run(self, args: list[str], extra_env: dict[str, str] | None = None) -> str:
        import os

        env = {**os.environ, **(extra_env or {})}
        env.setdefault("GIT_TERMINAL_PROMPT", "0")
        try:
            completed = subprocess.run(
                args,
                cwd=self.root,
                check=True,
                capture_output=True,
                text=True,
                env=env,
            )
        except subprocess.CalledProcessError as exc:
            raise GitError(exc.stderr.strip() or exc.stdout.strip() or str(exc)) from exc
        return completed.stdout
