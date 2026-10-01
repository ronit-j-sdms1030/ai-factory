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
        "vendor/interview-me/SKILL.md",
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
        "vendor/documentation-and-adrs/SKILL.md",
        "vendor/api-and-interface-design/SKILL.md",
    ),
    "ui": (
        "design-system.skill.md",
        "ui-screen-generation.skill.md",
        "vendor/open-design/craft/anti-ai-slop.md",
        "vendor/open-design/craft/typography.md",
        "vendor/open-design/craft/color.md",
        "vendor/open-design/craft/form-validation.md",
        "vendor/open-design/craft/state-coverage.md",
        "vendor/open-design/craft/accessibility-baseline.md",
        "vendor/open-design/design-systems/dashboard/DESIGN.md",
        "vendor/open-design/design-templates/dashboard/SKILL.md",
        "vendor/frontend-ui-engineering/SKILL.md",
    ),
    "devops": (
        "deployment-engineer.skill.md",
        "vendor/deployment-pipeline-design/SKILL.md",
        "vendor/ci-cd-and-automation/SKILL.md",
        "vendor/secrets-management/SKILL.md",
    ),
    "decomposer": (
        "department-routing.skill.md",
        "vendor/bmad-create-epics-and-stories/SKILL.md",
        "vendor/bmad-sprint-planning/SKILL.md",
        "vendor/planning-and-task-breakdown/SKILL.md",
    ),
    "qa": (
        "qa.skill.md",
        "vendor/bmad-qa-generate-e2e-tests/SKILL.md",
        "vendor/test-driven-development/SKILL.md",
        "vendor/e2e-testing-patterns/SKILL.md",
    ),
    "overview": (
        "vendor/bmad-product-brief/SKILL.md",
        "vendor/bmad-prfaq/SKILL.md",
        "vendor/avoid-ai-writing/SKILL.md",
    ),
    "build": (
        "build.skill.md",
        "vendor/incremental-implementation/SKILL.md",
    ),
    "review": (
        "review.skill.md",
        "vendor/code-review-and-quality/SKILL.md",
        "vendor/bmad-code-review/SKILL.md",
    ),
    "adversary": (
        "adversary.skill.md",
        "vendor/security-and-hardening/SKILL.md",
    ),
    "monitor": (
        "monitor.skill.md",
        "vendor/observability-and-instrumentation/SKILL.md",
    ),
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


_REQUIRED_MARKERS = {
    "design-system.skill.md": ("navigate(", "LucideReact"),
    "intake.skill.md": ("`GUESS:`",),
}


def _read(root: Path, relative: str) -> SkillFile:
    path = root / relative
    shipped = SHIPPED_SKILLS / relative
    chosen = path if path.is_file() else shipped
    markers = _REQUIRED_MARKERS.get(relative, ())
    if markers and chosen != shipped and shipped.is_file():
        if not all(marker in chosen.read_text(encoding="utf-8") for marker in markers):
            chosen = shipped
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


# Core files stay in the prefix. The rest are fetched via read_skill.
# Only agents with 4+ files save money this way.
FETCH_CORE_COUNT: dict[str, int] = {
    "intake": 1,
    "architect": 1,
    "ui": 2,
    "devops": 1,
    "decomposer": 1,
    "qa": 1,
}

UI_CORE = BUNDLES["ui"][: FETCH_CORE_COUNT["ui"]]
UI_CRAFT = BUNDLES["ui"][FETCH_CORE_COUNT["ui"] :]


def can_fetch(agent: str) -> bool:
    return agent in FETCH_CORE_COUNT


def _skill_key(relative: str) -> str:
    path = Path(relative)
    if path.name == "DESIGN.md":
        return f"{path.parent.name}-design"
    if path.name == "SKILL.md":
        return path.parent.name
    return path.stem


def _base(root: Path | None) -> Path:
    if root is None:
        return SHIPPED_SKILLS
    skills = Path(root) / "skills"
    return skills if skills.is_dir() else Path(root)


def craft_paths(agent: str) -> tuple[str, ...]:
    return BUNDLES[agent][FETCH_CORE_COUNT[agent] :]


def craft_index(agent: str) -> list[dict[str, str]]:
    return [{"name": _skill_key(relative), "path": relative} for relative in craft_paths(agent)]


def fetch_prompt(agent: str, *, root: Path | None = None) -> str:
    """Core rules plus a catalog. Extra files come through read_skill."""
    if agent not in BUNDLES or agent not in FETCH_CORE_COUNT:
        raise KeyError(f"no fetch prompt for agent {agent}")
    core_n = FETCH_CORE_COUNT[agent]
    core = []
    for relative in BUNDLES[agent][:core_n]:
        item = _read(_base(root), relative)
        core.append(f"# SKILL {item.name} ({item.source})\n\n{item.content.rstrip()}\n")
    catalog = "\n".join(f"- `{row['name']}` — {row['path']}" for row in craft_index(agent))
    if agent == "ui":
        header = (
            "One screen only. Core rules are below. Call `read_skill` for at most two "
            "craft files this screen needs, then output JSX only.\n\n"
            f"Available craft files:\n{catalog}\n"
        )
    else:
        header = (
            f"Core {agent} rules are below. Call `read_skill` for at most two extra "
            f"skill files this step needs, then answer.\n\n"
            f"Available skill files:\n{catalog}\n"
        )
    return header + "\n---\n\n" + "\n---\n\n".join(core)


def fetch_tools(agent: str) -> list[dict[str, object]]:
    names = [row["name"] for row in craft_index(agent)]
    return [
        {
            "type": "function",
            "function": {
                "name": "read_skill",
                "description": f"Load one extra {agent} skill file. At most two per call.",
                "parameters": {
                    "type": "object",
                    "properties": {"name": {"type": "string", "enum": names}},
                    "required": ["name"],
                },
            },
        }
    ]


def read_craft(agent: str, name: str, *, root: Path | None = None) -> str:
    wanted = (name or "").strip()
    for relative in craft_paths(agent):
        if _skill_key(relative) == wanted:
            return _read(_base(root), relative).content
    raise IncompleteSkillFile(f"unknown {agent} skill file: {wanted}")


def execute_fetch_tool(
    agent: str, name: str, args: dict[str, object], *, root: Path | None = None
) -> str:
    if name != "read_skill":
        return f"unknown tool {name}"
    try:
        return read_craft(agent, str(args.get("name") or ""), root=root)
    except IncompleteSkillFile as exc:
        return str(exc)


def ui_prompt(*, root: Path | None = None) -> str:
    return fetch_prompt("ui", root=root)


def ui_tools() -> list[dict[str, object]]:
    return fetch_tools("ui")


def execute_ui_tool(name: str, args: dict[str, object], *, root: Path | None = None) -> str:
    return execute_fetch_tool("ui", name, args, root=root)
