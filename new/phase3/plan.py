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
    tickets: list[dict[str, Any]] | None = None,
    tests: list[dict[str, Any]] | None = None,
    overview_md: str | None = None,
    devops_note: str = "",
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
    tickets = tickets or decomposer.tickets(
        requirement_id,
        brd_text,
        screens,
        profile,
        skill=decomp.content,
        architecture_text=architecture_text,
    )
    names = decomposer._entity_names(brd_text, architecture_text) or routing.entities_from_text(
        architecture_text, brd_text
    )
    entities, collisions = routing.repair_entities(names)
    owners = routing.assign_owners(entities, tickets)
    tests = tests or qa.cases(brd_text, screens, profile, skill=qa_bundle.content)
    overview_md = overview_md or overview.write(
        requirement_id, brd_text, skill=overview_bundle.content
    )
    files: dict[str, str] = {}
    scan = None
    if not skipped:
        files.update(sprint0.files(requirement_id, profile, reviewers=reviewers))
        scan = sprint0.scan_files(
            {path: content for path, content in files.items() if "/sprint0/" in path}
        )
    sprint = sprint0.summary(profile, skipped, scan=scan)
    if devops_note:
        files[f"requirements/{requirement_id}/plan/sprint0/NOTES.md"] = devops_note.rstrip() + "\n"
        sprint = {**sprint, "note": devops_note[:400]}
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
        "team_reports": _team_reports(requirement_id, tickets, screens),
    }


def _team_reports(
    requirement_id: str,
    tickets: list[dict[str, Any]],
    screens: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for ticket in tickets:
        grouped.setdefault(ticket["department"], []).append(ticket)
    screen_names = [str(screen.get("name") or "Screen") for screen in screens]
    reports = []
    for team, items in grouped.items():
        owned = [
            str(item.get("screen") or item["title"][: -len(" screen")])
            for item in items
            if str(item.get("title") or "").lower().endswith(" screen")
        ]
        reports.append(
            {
                "team": team,
                "summary": (
                    f"{len(items)} tickets in the single app at app/{requirement_id}/. "
                    "Streams own folders; they do not ship as separate products."
                ),
                "tickets": [item["id"] for item in items],
                "screens": owned if team == "development" else [],
                "productScreens": screen_names,
                "integratesInto": f"app/{requirement_id}",
                "contract": (
                    "One process, one SQLite/Postgres, one React shell. "
                    "development writes src/ui + src/api. "
                    "ai writes src/ai and is mounted at /api/ai on the same server."
                ),
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
