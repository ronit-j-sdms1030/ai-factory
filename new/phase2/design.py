"""Assemble architecture and screens into the design artefact set."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from phase2 import architect, contract as product_contract, coverage, preview, ui
import stack_profiles
from stack_profiles import StackProfile
import skill_registry


DESIGN_SYSTEM_REL = "skills/design-system.skill.md"


def clarification_needed(brd_text: str) -> str | None:
    if stack_profiles.delivery_fit(brd_text)["core_outside"]:
        return stack_profiles.boundary_question(brd_text)
    if not re.search(r"^##(?:\s+\d+\.)?\s+Page behaviour\b", brd_text, re.M | re.I):
        return "Which pages should a reviewer click through for this BRD?"
    if "### " not in brd_text:
        return "Which capabilities should become independently testable requirements?"
    for page in coverage.pages_from_brd(brd_text):
        desc = str(page.get("description") or "").strip().lower()
        if desc in {"tbd", "todo", "unknown", "?", "n/a"}:
            return f"What should a reviewer do on {page.get('id')}?"
    return None


def build(
    requirement_id: str,
    brd_text: str,
    skill_text: str,
    *,
    root: Path | None = None,
    extra_pages: list[dict[str, str]] | None = None,
    architect_note: str = "",
    screen_sources: dict[str, str] | None = None,
    refresh: str = "both",
    prior_architecture: str = "",
    prior_decision: dict[str, Any] | None = None,
) -> dict[str, Any]:
    question = clarification_needed(brd_text)
    if question:
        return {"clarification": question}
    arch_bundle = skill_registry.load_bundle("architect", root=root)
    ui_bundle = skill_registry.load_bundle("ui", root=root)
    skill_registry.require(arch_bundle.content, "architect")
    skill_registry.require(ui_bundle.content + "\n" + skill_text, "ui")
    if refresh == "ui" and prior_architecture:
        profile = stack_profiles.get(
            str(((prior_decision or {}).get("profile") or {}).get("id") or "node")
        )
        decision = dict(prior_decision or {"profile": profile.dump(), "adrs": [], "extra_pages": []})
        architecture = prior_architecture
    else:
        decision = architect.decide(requirement_id, brd_text, skill=arch_bundle.content)
        decision["brd_excerpt"] = (brd_text or "").split("## Open questions", 1)[0].strip()
        profile = stack_profiles.get(str(decision["profile"]["id"]))
        architecture = architect.render(decision, note=architect_note)
    extra = [] if refresh == "architecture" else list(decision.get("extra_pages") or [])
    for page in extra_pages or []:
        ident = str(page.get("id") or "").strip()
        if ident:
            extra.append(
                {
                    "id": ident[:40],
                    "description": str(page.get("description") or ident)[:220],
                }
            )
    screen_entities = list(decision.get("entities") or [])
    if refresh == "ui":
        screen_entities = list((prior_decision or {}).get("entities") or screen_entities)
    # A model screen that is missing the locked fields is discarded. The
    # fallback then draws those fields, so the UI cannot drift off the BRD.
    if product_contract.problems(brd_text, screen_entities):
        screen_entities = list(
            architect.decide(requirement_id, brd_text).get("entities") or []
        )
    if refresh == "architecture":
        # Screens wait until BA has signed Gate 3 — Architect reviews stack/ADRs only.
        generated = {
            "screens": [],
            "pages": [],
            "repaired": [],
            "extras": [],
            "conform_failures": [],
            "preview_index": "",
        }
    else:
        generated = ui.build_screens(
            brd_text,
            skill_text + "\n" + ui_bundle.content,
            extra_pages=extra,
            sources=screen_sources,
            entities=screen_entities,
        )
    files = {
        f"requirements/{requirement_id}/design/architecture.md": architecture,
        f"requirements/{requirement_id}/design/architecture.json": json.dumps(
            {key: value for key, value in decision.items() if key != "architecture"},
            indent=2,
        )
        + "\n",
        f"requirements/{requirement_id}/design/stack-profile.json": json.dumps(profile.dump(), indent=2) + "\n",
        DESIGN_SYSTEM_REL: skill_text,
        f"requirements/{requirement_id}/design/preview.html": preview.document(
            requirement_id, generated["screens"]
        ),
        f"requirements/{requirement_id}/design/coverage.json": json.dumps(
            {
                "repaired": generated["repaired"],
                "extras": generated["extras"],
                "pages": generated["pages"],
            },
            indent=2,
        )
        + "\n",
    }
    for adr in decision.get("adrs") or []:
        files[f"requirements/{requirement_id}/design/adrs/{adr['id']}.md"] = adr["body"]
    files.update(skill_registry.snapshot_paths(requirement_id, "design", arch_bundle))
    files.update(skill_registry.snapshot_paths(requirement_id, "design", ui_bundle))
    for screen in generated["screens"]:
        files[f"requirements/{requirement_id}/design/ui/{screen['name']}.jsx"] = screen["source"]
    if refresh != "ui":
        issues = product_contract.problems(brd_text, decision.get("entities"))
        if issues:
            raise RuntimeError(
                "architecture does not match the BRD data model: " + "; ".join(issues)
            )
    return {
        "profile": profile.dump(),
        "architecture": architecture,
        "decision": decision,
        "adr": (decision.get("adrs") or [{}])[0].get("body") or "",
        "screens": generated["screens"],
        "coverage": {
            "repaired": generated["repaired"],
            "extras": generated["extras"],
            "pages": generated["pages"],
        },
        "report": architect.dump_report(architecture, profile, decision),
        "preview_url": preview.url(requirement_id),
        "preview_host": preview.host(),
        "files": files,
    }
