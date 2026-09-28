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
        "interview-me",
        "documentation-and-adrs",
        "api-and-interface-design",
        "planning-and-task-breakdown",
        "test-driven-development",
        "ci-cd-and-automation",
        "code-review-and-quality",
        "security-and-hardening",
        "incremental-implementation",
        "observability-and-instrumentation",
        "frontend-ui-engineering",
        "secrets-management",
        "e2e-testing-patterns",
        "avoid-ai-writing",
        "bmad-code-review",
    }
    for item in payload["skills"]:
        data = (root / "skills/vendor" / item["target"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == item["sha256"]
    for agent in ("intake", "brd", "architect", "ui", "devops", "decomposer", "qa", "overview"):
        bundle = skill_registry.load_bundle(agent)
        assert bundle.files
        assert "SKILL" in bundle.content


def test_ui_fetch_prompt_is_smaller_than_full_bundle():
    full = skill_registry.load_bundle("ui").content
    slim = skill_registry.ui_prompt()
    assert len(slim) < len(full) * 0.35
    assert "read_skill" in slim
    assert "typography" in slim
    text = skill_registry.execute_ui_tool("read_skill", {"name": "typography"})
    assert "font" in text.lower() or "type" in text.lower()
    assert "unknown" in skill_registry.execute_ui_tool("read_skill", {"name": "nope"})


def test_fetch_covers_fat_agents_and_skips_thin_ones():
    assert skill_registry.can_fetch("intake")
    assert skill_registry.can_fetch("architect")
    assert skill_registry.can_fetch("qa")
    assert not skill_registry.can_fetch("brd")
    assert not skill_registry.can_fetch("review")
    for agent in skill_registry.FETCH_CORE_COUNT:
        full = skill_registry.load_bundle(agent).content
        slim = skill_registry.fetch_prompt(agent)
        assert len(slim) < len(full)
        assert "read_skill" in slim
    text = skill_registry.execute_fetch_tool("intake", "read_skill", {"name": "interview-me"})
    assert len(text) > 80


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
