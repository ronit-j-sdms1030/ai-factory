"""Assemble architecture and screens into the design artefact set."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from phase2 import architect, coverage, preview, ui
from stack_profiles import StackProfile
import skill_registry


DESIGN_SYSTEM_REL = "skills/design-system.skill.md"


def clarification_needed(brd_text: str) -> str | None:
    if not re.search(r"^##(?:\s+\d+\.)?\s+Page behaviour\b", brd_text, re.M | re.I):
        return "Which pages should a reviewer click through for this BRD?"
    if "### " not in brd_text:
        return "Which capabilities should become independently testable requirements?"
    return None


def build(
    requirement_id: str,
    brd_text: str,
    skill_text: str,
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    question = clarification_needed(brd_text)
    if question:
        return {"clarification": question}
    arch_bundle = skill_registry.load_bundle("architect", root=root)
    ui_bundle = skill_registry.load_bundle("ui", root=root)
    skill_registry.require(arch_bundle.content, "architect")
    skill_registry.require(ui_bundle.content + "\n" + skill_text, "ui")
    pages = coverage.pages_from_brd(brd_text)
    extra = [{"id": "AdminCancel", "description": "Admin cancels any booking"}]
    profile: StackProfile = architect.lock_profile(brd_text, skill=arch_bundle.content)
    architecture = architect.architecture_markdown(
        requirement_id, brd_text, profile, pages, skill=arch_bundle.content
    )
    adr = architect.adr_overlap(requirement_id, profile)
    generated = ui.build_screens(
        brd_text, skill_text + "\n" + ui_bundle.content, extra_pages=extra
    )
    files = {
        f"requirements/{requirement_id}/design/architecture.md": architecture,
        f"requirements/{requirement_id}/design/adrs/ADR-001.md": adr,
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
    files.update(skill_registry.snapshot_paths(requirement_id, "design", arch_bundle))
    files.update(skill_registry.snapshot_paths(requirement_id, "design", ui_bundle))
    for screen in generated["screens"]:
        files[f"requirements/{requirement_id}/design/ui/{screen['name']}.jsx"] = screen["source"]
    return {
        "profile": profile.dump(),
        "architecture": architecture,
        "adr": adr,
        "screens": generated["screens"],
        "coverage": {
            "repaired": generated["repaired"],
            "extras": generated["extras"],
            "pages": generated["pages"],
        },
        "report": architect.dump_report(architecture, profile),
        "preview_url": preview.url(requirement_id),
        "files": files,
    }
