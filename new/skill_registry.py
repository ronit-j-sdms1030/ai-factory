"""Skill files the Phase 1–3 agents are compiled against.

Factory-owned files live under ``skills/``. Third-party SKILL.md files are
vendored under ``skills/vendor/`` from the pinned manifest. Every agent receives
its bundle whole, as a stable prefix. A snapshot of that bundle is committed
beside the artefact it produced.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

FACTORY_ROOT = Path(__file__).resolve().parent
SHIPPED_SKILLS = FACTORY_ROOT / "skills"
VENDOR_SKILLS = SHIPPED_SKILLS / "vendor"

# Agent -> relative skill paths (factory first, then vendor).
BUNDLES: dict[str, tuple[str, ...]] = {
    "intake": (
        "intake.skill.md",
        "vendor/bmad-agent-analyst/SKILL.md",
        "vendor/bmad-advanced-elicitation/SKILL.md",
        "vendor/spec-driven-development/SKILL.md",
    ),
    "brd": (
        "brd.template.md",
        "vendor/bmad-agent-pm/SKILL.md",
        "vendor/bmad-prd/SKILL.md",
    ),
    "architect": (
        "stack-profiles.skill.md",
        "vendor/bmad-agent-architect/SKILL.md",
        "vendor/bmad-architecture/SKILL.md",
        "vendor/bmad-spec/SKILL.md",
    ),
    "ui": (
        "design-system.skill.md",
        "vendor/bmad-agent-ux-designer/SKILL.md",
        "vendor/bmad-ux/SKILL.md",
    ),
    "devops": (
        "deployment-engineer.skill.md",
        "vendor/deployment-pipeline-design/SKILL.md",
    ),
    "decomposer": (
        "department-routing.skill.md",
        "vendor/bmad-create-epics-and-stories/SKILL.md",
        "vendor/bmad-sprint-planning/SKILL.md",
    ),
    "qa": (
        "vendor/bmad-qa-generate-e2e-tests/SKILL.md",
    ),
    "overview": (
        "vendor/bmad-product-brief/SKILL.md",
        "vendor/bmad-prfaq/SKILL.md",
    ),
    "build": ("build.skill.md",),
    "review": ("review.skill.md",),
    "adversary": ("adversary.skill.md",),
    "monitor": ("monitor.skill.md",),
}


class IncompleteSkillFile(Exception):
    """A required skill file is missing or empty."""


@dataclass(frozen=True)
class SkillFile:
    name: str
    content: str
    path: str
    version: str
    source: str  # "factory" | "vendor"


@dataclass(frozen=True)
class SkillBundle:
    agent: str
    files: tuple[SkillFile, ...]
    version: str

    @property
    def content(self) -> str:
        parts = []
        for item in self.files:
            parts.append(f"# SKILL {item.name} ({item.source})\n\n{item.content.rstrip()}\n")
        return "\n---\n\n".join(parts)

    @property
    def names(self) -> list[str]:
        return [item.name for item in self.files]


def _read(root: Path, relative: str) -> SkillFile:
    path = root / relative
    shipped = SHIPPED_SKILLS / relative
    chosen = path if path.is_file() else shipped
    if not chosen.is_file() or not chosen.read_text(encoding="utf-8").strip():
        raise IncompleteSkillFile(f"skill file missing or empty: {relative}")
    source = "vendor" if "vendor/" in relative.replace("\\", "/") else "factory"
    return SkillFile(
        name=Path(relative).parent.name if relative.endswith("SKILL.md") else Path(relative).stem,
        content=chosen.read_text(encoding="utf-8"),
        path=relative,
        version="shipped",
        source=source,
    )


def load_bundle(agent: str, *, root: Path | None = None, version: str = "shipped") -> SkillBundle:
    if agent not in BUNDLES:
        raise KeyError(f"no skill bundle for agent {agent}")
    base = Path(root) if root is not None else FACTORY_ROOT
    files = []
    for relative in BUNDLES[agent]:
        item = _read(base / "skills" if (base / "skills").is_dir() else base, relative)
        files.append(
            SkillFile(
                name=item.name,
                content=item.content,
                path=item.path,
                version=version,
                source=item.source,
            )
        )
    return SkillBundle(agent=agent, files=tuple(files), version=version)


def snapshot_paths(requirement_id: str, stage: str, bundle: SkillBundle) -> dict[str, str]:
    """Commit the rules beside the artefact they produced."""
    files = {}
    for item in bundle.files:
        name = Path(item.path).name
        files[f"requirements/{requirement_id}/{stage}/skills/{item.name}/{name}"] = item.content
    files[f"requirements/{requirement_id}/{stage}/skills/{bundle.agent}.BUNDLE.md"] = bundle.content
    return files


def versions(bundle: SkillBundle) -> dict[str, str]:
    return {item.name: bundle.version for item in bundle.files}


def require(skill: str, agent: str) -> str:
    text = (skill or "").strip()
    if len(text) < 80:
        raise IncompleteSkillFile(f"{agent} invoked without its skill file")
    return text
