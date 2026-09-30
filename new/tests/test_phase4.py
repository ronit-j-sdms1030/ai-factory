"""Phase 4 — allow-listed stubs, then Gate 5 merge."""

from __future__ import annotations

from pathlib import Path

import pytest

import gate_engine
from phase1 import directory
from phase1.platform import Phase1
from phase4 import ci as phase4_ci
from tests.test_phase3 import reach_plan, sign_gate_4


def test_gate_4_starts_an_allowlisted_build(tmp_path: Path):
    p1 = Phase1(tmp_path)
    rid = reach_plan(p1)
    last = sign_gate_4(p1, rid)
    assert last["phase"] == "awaiting_gate_5"
    assert last["awaiting"] == 5
    assert last["build"]["sandbox"]["sandbox"] == "local"
    assert last["build"]["sandbox"]["status"] == "substitute"
    assert last["build"]["ok"] is True
    names = [row["name"] for row in last["build"]["scans"]]
    assert names == [
        "gitleaks",
        "bandit",
        "eslint-plugin-security",
        "opengrep",
        "trivy",
        "checkov",
        "syft",
        "scancode",
    ]
    assert last["build"]["engines"]
    pipeline = last["build"]["pipeline"]
    assert pipeline["ok"] is True
    assert pipeline["mode"] in {"substitute", "vendor", "mixed"}
    missing = [
        row for row in pipeline["inventory"] if str(row.get("status") or "") != "available"
    ]
    if not missing:
        assert pipeline["mode"] == "vendor"
        assert "Vendor scanner binaries present" in pipeline["summary"]
    elif len(missing) == len(pipeline["inventory"]):
        assert pipeline["mode"] == "substitute"
        assert "Vendor binaries absent" in pipeline["summary"]
    else:
        assert pipeline["mode"] == "mixed"
    assert pipeline["flowchart"][:3] == ["agents", "ci", "scans"]
    assert pipeline["tickets"]
    first = pipeline["tickets"][0]
    assert set(first["stages"]) >= {"agents", "ci", "scans", "review", "adversary", "coverage"}
    assert first["stages"]["ci"]["ok"] is True
    assert first["stages"]["ci"]["mode"] == "substitute"
    assert first["stages"]["scans"]["mode"] == pipeline["mode"]
    assert p1.git.exists(f"requirements/{rid}/build/PIPELINE.md", f"build/{rid}")
    ticket = last["tickets"][0]
    branch = f"feat/{ticket['id']}"
    assert p1.git.exists(f"src/api/{ticket['id']}.ts", branch) or p1.git.exists(
        "prisma/schema.prisma", branch
    )
    assert p1.git.exists(f"requirements/{rid}/build/ci.yml", f"build/{rid}")
    assert p1.git.exists(f"app/{rid}/server.js", f"build/{rid}")
    assert p1.git.exists(f"app/{rid}/prisma/schema.prisma", f"build/{rid}")
    assert p1.git.exists(f"app/{rid}/public/index.html", f"build/{rid}")
    schema = p1.git.read(f"app/{rid}/prisma/schema.prisma", f"build/{rid}")
    assert "model " in schema
    html = p1.git.read(f"app/{rid}/public/index.html", f"build/{rid}")
    assert "function " in html


def test_local_ci_substitute_records_checks():
    report = phase4_ci.run_local(
        {"src/api/a.ts": "export const ok = true;\n"},
        tests=[{"id": "T1", "critical": True}],
    )
    assert report["mode"] == "substitute"
    assert report["ok"] is True
    assert report["verdict"] == "pass"
    assert {c["name"] for c in report["checks"]} >= {
        "compile_or_parse",
        "unit_integration_coverage",
    }


def test_gate_5_blocked_when_build_has_critical_findings(tmp_path: Path):
    p1 = Phase1(tmp_path)
    rid = reach_plan(p1)
    sign_gate_4(p1, rid)
    body = p1._load(rid)
    body["build"] = {
        **(body.get("build") or {}),
        "ok": False,
        "blocking": [{"ticket": "W-bad", "scans": {"ok": False}}],
    }
    p1._save(body, event="test_block", at=p1.clock())
    with pytest.raises(gate_engine.GateRefused, match="Gate 5 blocked"):
        p1.decide(rid, directory.actor("u-se"), "approve")
    # request_changes must still be allowed so the loop can rebuild
    revised = p1.decide(rid, directory.actor("u-se"), "request_changes")
    assert revised["awaiting"] == 5


def test_gate_5_merges_feature_branches(tmp_path: Path):
    p1 = Phase1(tmp_path)
    rid = reach_plan(p1)
    sign_gate_4(p1, rid)
    from phase1 import codegen as codegen_jobs

    preview = codegen_jobs.ensure_merged_preview(rid, git=p1.git, row=p1._load(rid))
    assert preview["status"] == "done"
    assert preview["totalFiles"] >= 1
    assert any(
        str(f.get("path") or "").startswith(f"app/{rid}/") for f in preview.get("files") or []
    )
    merged = p1.decide(rid, directory.actor("u-se"), "approve")
    assert merged["awaiting"] == 6
    assert merged["phase"] == "awaiting_gate_6"
    ticket = merged["tickets"][0]
    path = f"src/api/{ticket['id']}.ts"
    alt = "prisma/schema.prisma"
    assert p1.git.exists(path, "main") or p1.git.exists(alt, "main")
    assert p1.git.exists(f"requirements/{rid}/uat_deploy/STATUS.md", f"uat/{rid}")
    assert p1.git.exists(f"requirements/{rid}/uat_deploy/locustfile.py", f"uat/{rid}")


def test_local_uat_and_release_gates_complete_the_run(tmp_path: Path):
    p1 = Phase1(tmp_path)
    rid = reach_plan(p1)
    sign_gate_4(p1, rid)
    p1.decide(rid, directory.actor("u-se"), "approve")
    uat = p1.decide(rid, directory.actor("u-requester"), "approve")
    assert uat["awaiting"] == 7
    assert uat["phase"] == "awaiting_gate_7"
    done = p1.decide(rid, directory.actor("u-rm"), "approve")
    assert done["awaiting"] is None
    assert done["phase"] == "complete"
    assert p1.git.exists(f"requirements/{rid}/release/STATUS.md", "main")
    assert p1.git.exists(f"requirements/{rid}/release/kyverno.yaml", "main")
    assert p1.git.exists(f"requirements/{rid}/release/flagd.json", "main")
    catalog = p1.catalog()
    assert any(item["metadata"]["title"] == rid for item in catalog)
    cr = p1.submit_change_request(
        directory.actor("u-requester"), rid, "latency regression", "api timeout"
    )
    assert cr["brd_sha"]
    assert cr["follow_on"] != rid
    assert p1.list_change_requests()
