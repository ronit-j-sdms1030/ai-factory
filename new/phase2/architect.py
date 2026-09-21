"""Architect Agent — one locked stack profile, ADRs, architecture.md.

Precedent retrieval is disabled. The architecture is derived from this BRD.
"""

from __future__ import annotations

from typing import Any

import stack_profiles
from stack_profiles import StackProfile


def lock_profile(brd_text: str, *, skill: str = "") -> StackProfile:
    if skill:
        import skill_registry

        skill_registry.require(skill, "architect")
    return stack_profiles.choose(brd_text)


def architecture_markdown(
    requirement_id: str,
    brd_text: str,
    profile: StackProfile,
    pages: list[dict[str, str]],
    *,
    skill: str = "",
) -> str:
    del skill
    modules = "\n".join(f"- `{page['id']}` — {page['description']}" for page in pages)
    mermaid = "\n".join(
        [
            "```mermaid",
            "flowchart LR",
            "  UI[React screens] --> API[" + profile.api + "]",
            "  API --> DB[" + profile.database + "]",
            "```",
        ]
    )
    return "\n".join(
        [
            f"# Architecture — {requirement_id}",
            "",
            f"Locked stack profile: **{profile.id}**.",
            "",
            "## Stack",
            "",
            f"- Frontend: {profile.frontend}",
            f"- API: {profile.api}",
            f"- Data access: {profile.data_access}",
            f"- Migrations: {profile.migrations}",
            f"- Tests: {profile.tests}",
            f"- Lint: {profile.lint}",
            f"- Database: {profile.database}",
            "",
            "## Modules",
            "",
            modules or "- Derived from the approved BRD.",
            "",
            "## Data model",
            "",
            "Entities follow the BRD outline. Shared records have one owning department.",
            "",
            "## API contracts",
            "",
            "Each module exposes create/read/cancel over HTTPS. Identity is Entra SSO.",
            "",
            "## Diagram",
            "",
            mermaid,
            "",
            "## Non-functional",
            "",
            "- Browser only. No third runtime.",
            "- Personal data is masked before model egress.",
            "",
            "## Source BRD excerpt",
            "",
            brd_text.split("## Open questions", 1)[0].strip()[:2000],
            "",
        ]
    )


def adr_overlap(requirement_id: str, profile: StackProfile) -> str:
    return "\n".join(
        [
            f"# ADR-001 — Overlap prevention is a {profile.database} constraint",
            "",
            f"Requirement: {requirement_id}",
            "",
            "## Context",
            "",
            "Two people clicking book in the same second will both pass an",
            "application-level check and both insert.",
            "",
            "## Decision",
            "",
            f"Enforce exclusion in {profile.database}, not in {profile.api} application code.",
            f"Migrations use {profile.migrations}. Tests use {profile.tests}.",
            "",
            "## Consequences",
            "",
            "Concurrency tests must hit the database. A suite that only mocks the",
            "API will pass and still lose the race in production.",
            "",
        ]
    )


def dump_report(architecture: str, profile: StackProfile) -> dict[str, Any]:
    return {
        "objective": architecture.split("\n", 1)[0].lstrip("# ").strip(),
        "architecture": architecture,
        "architectureDiagram": "flowchart LR\n  UI --> API --> DB",
        "techStack": [
            {"layer": "frontend", "choice": profile.frontend, "rationale": f"locked {profile.id} profile"},
            {"layer": "api", "choice": profile.api, "rationale": f"locked {profile.id} profile"},
            {"layer": "data", "choice": profile.data_access, "rationale": f"locked {profile.id} profile"},
            {"layer": "database", "choice": profile.database, "rationale": f"locked {profile.id} profile"},
        ],
        "securityDesign": ["Entra SSO", "No secrets in images"],
        "openQuestions": [],
        "assumptions": ["Precedent retrieval stayed disabled for this architecture."],
    }
