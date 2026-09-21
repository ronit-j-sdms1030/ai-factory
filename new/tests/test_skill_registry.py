"""Skill files are on disk, pinned, and snapshotted beside artefacts."""

from __future__ import annotations

from pathlib import Path

import skill_registry
from phase1 import directory
from phase1.platform import Phase1
from tests.test_phase2 import reach_design
from tests.test_phase3 import reach_plan


def test_vendor_manifest_checksums_match_disk():
    import hashlib
    import json

    root = skill_registry.FACTORY_ROOT
    payload = json.loads((root / "skills/vendor-manifest.json").read_text(encoding="utf-8"))
    names = {item["name"] for item in payload["skills"]}
    assert names == {
        "bmad-agent-analyst",
        "bmad-agent-pm",
        "bmad-prd",
        "bmad-advanced-elicitation",
        "spec-driven-development",
        "bmad-agent-architect",
        "bmad-agent-ux-designer",
        "bmad-architecture",
        "bmad-ux",
        "bmad-spec",
        "bmad-create-epics-and-stories",
        "bmad-sprint-planning",
        "bmad-qa-generate-e2e-tests",
        "bmad-product-brief",
        "bmad-prfaq",
        "deployment-pipeline-design",
    }
    for item in payload["skills"]:
        data = (root / "skills/vendor" / item["target"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == item["sha256"]
    for agent in ("intake", "brd", "architect", "ui", "devops", "decomposer", "qa", "overview"):
        bundle = skill_registry.load_bundle(agent)
        assert bundle.files
        assert "SKILL" in bundle.content


def test_intake_and_brd_snapshot_skill_files(tmp_path: Path):
    p1 = Phase1(tmp_path)
    run = p1.submit(directory.actor("u-requester"), "full_governance", "Book meeting rooms.")
    rid = run["requirement"]["id"]
    from tests.test_phase1 import finish_intake

    finish_intake(p1, rid)
    assert p1.git.exists(f"requirements/{rid}/scope/skills/intake.BUNDLE.md", f"scope/{rid}")
    p1.decide(rid, directory.actor("u-po"), "approve")
    assert p1.git.exists(f"requirements/{rid}/brd/skills/brd.BUNDLE.md", f"brd/{rid}")


def test_design_snapshots_skills_and_unique_preview(tmp_path: Path):
    p1 = Phase1(tmp_path)
    rid = reach_design(p1)
    run = p1.get(rid)
    assert run["preview_url"] == f"/preview/{rid}"
    html = p1.preview_document(rid)
    assert rid in html
    assert p1.git.exists(f"requirements/{rid}/design/skills/architect.BUNDLE.md", f"design/{rid}")
    assert p1.git.exists(f"requirements/{rid}/design/preview.html", f"design/{rid}")


def test_plan_snapshots_skills_and_sprint0_allowlist(tmp_path: Path):
    p1 = Phase1(tmp_path)
    rid = reach_plan(p1)
    assert p1.git.exists(f"requirements/{rid}/plan/skills/devops.BUNDLE.md", f"plan/{rid}")
    allow = p1.git.read(f"requirements/{rid}/plan/sprint0/allowlist.txt", f"plan/{rid}")
    assert "react" in allow
    owners = p1.git.read(f"requirements/{rid}/plan/sprint0/CODEOWNERS", f"plan/{rid}")
    assert "@u-tl" in owners or "@tech-lead" in owners
    ci = p1.git.read(f"requirements/{rid}/plan/sprint0/ci.yml", f"plan/{rid}")
    assert "allowlist" in ci
    status = p1.git.read(f"requirements/{rid}/plan/sprint0/STATUS.md", f"plan/{rid}")
    assert "green" in status.lower()
