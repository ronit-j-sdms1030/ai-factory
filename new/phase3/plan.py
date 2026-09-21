"""Assemble Sprint 0, tickets, tests and overview into the plan artefact."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import department_routing as routing
import skill_registry
from phase3 import decomposer, overview, qa, sprint0
from stack_profiles import StackProfile, as_public, get as get_profile


def build(
    requirement_id: str,
    *,
    template: str,
    brd_text: str,
    architecture_text: str,
    screens: list[dict[str, Any]],
    stack_profile: dict[str, Any] | StackProfile,
    reviewers: list[str] | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    profile = (
        stack_profile
        if isinstance(stack_profile, StackProfile)
        else get_profile(str((stack_profile or {}).get("id") or "node"))
    )
    skipped = sprint0.skip_sprint0(template)
    devops = skill_registry.load_bundle("devops", root=root)
    decomp = skill_registry.load_bundle("decomposer", root=root)
    qa_bundle = skill_registry.load_bundle("qa", root=root)
    overview_bundle = skill_registry.load_bundle("overview", root=root)
    skill_registry.require(devops.content, "devops")
    skill_registry.require(decomp.content, "decomposer")
    skill_registry.require(qa_bundle.content, "qa")
    skill_registry.require(overview_bundle.content, "overview")
    tickets = decomposer.tickets(
        requirement_id, brd_text, screens, profile, skill=decomp.content
    )
    entities, collisions = routing.repair_entities(
        routing.entities_from_text(architecture_text, brd_text)
        or ["Room", "Booking", "User", "AuditEntry"]
    )
    owners = routing.assign_owners(entities, tickets)
    tests = qa.cases(brd_text, screens, profile, skill=qa_bundle.content)
    overview_md = overview.write(requirement_id, brd_text, skill=overview_bundle.content)
    files: dict[str, str] = {}
    scan = None
    if not skipped:
        files.update(sprint0.files(requirement_id, profile, reviewers=reviewers))
        scan = sprint0.scan_files(
            {path: content for path, content in files.items() if "/sprint0/" in path}
        )
    sprint = sprint0.summary(profile, skipped, scan=scan)
    files.update(skill_registry.snapshot_paths(requirement_id, "plan", devops))
    files.update(skill_registry.snapshot_paths(requirement_id, "plan", decomp))
    files.update(skill_registry.snapshot_paths(requirement_id, "plan", qa_bundle))
    files.update(skill_registry.snapshot_paths(requirement_id, "plan", overview_bundle))
    plan_md = _plan_markdown(
        requirement_id, profile, sprint, tickets, tests, owners, collisions
    )
    files.update(
        {
            f"requirements/{requirement_id}/plan/plan.md": plan_md,
            f"requirements/{requirement_id}/plan/tickets.json": json.dumps(tickets, indent=2)
            + "\n",
            f"requirements/{requirement_id}/plan/tests.md": qa.render(tests),
            f"requirements/{requirement_id}/plan/overview.md": overview_md,
            f"requirements/{requirement_id}/plan/entities.json": json.dumps(
                {"owners": owners, "collisions": collisions}, indent=2
            )
            + "\n",
        }
    )
    streams = sorted({t["department"] for t in tickets})
    return {
        "plan": plan_md,
        "tickets": tickets,
        "tests": tests,
        "overview": overview_md,
        "sprint0": sprint,
        "entities": {"owners": owners, "collisions": collisions},
        "streams": streams,
        "profile": as_public(profile),
        "files": files,
        "team_reports": _team_reports(tickets),
    }


def _team_reports(tickets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for ticket in tickets:
        grouped.setdefault(ticket["department"], []).append(ticket)
    reports = []
    for team, items in grouped.items():
        reports.append(
            {
                "team": team,
                "summary": f"{len(items)} tickets for {team}",
                "tickets": [item["id"] for item in items],
            }
        )
    return reports


def _plan_markdown(
    requirement_id: str,
    profile: StackProfile,
    sprint: dict[str, Any],
    tickets: list[dict[str, Any]],
    tests: list[dict[str, Any]],
    owners: dict[str, str],
    collisions: list[dict[str, str]],
) -> str:
    ticket_lines = "\n".join(
        f"- `{t['id']}` [{t['department']}] {t['title']} "
        f"(paths: {', '.join(t['paths'])}; depends: {', '.join(t['depends_on']) or 'none'})"
        for t in tickets
    )
    entity_lines = "\n".join(f"- `{name}` owned by {dept}" for name, dept in owners.items())
    collision_note = (
        "\n".join(
            f"- repaired {' / '.join(c['aliases'])} → `{c['canonical']}`" for c in collisions
        )
        or "- none"
    )
    return "\n".join(
        [
            f"# Plan — {requirement_id}",
            "",
            f"Locked profile: **{profile.id}** ({profile.tests} tests, {profile.migrations} migrations).",
            "",
            f"Sprint 0: **{sprint.get('status')}**.",
            "",
            "## Tickets",
            "",
            ticket_lines,
            "",
            "## Entity ownership",
            "",
            entity_lines or "- none extracted; development owns the default set.",
            "",
            "## Naming repairs",
            "",
            collision_note,
            "",
            f"## Tests ({len(tests)}, before code)",
            "",
            qa.render(tests),
            "",
        ]
    )
