"""Phase 4 — allow-listed stubs, then Gate 5 merge."""

from __future__ import annotations

from pathlib import Path

from phase1 import directory
from phase1.platform import Phase1
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
    ticket = last["tickets"][0]
    branch = f"feat/{ticket['id']}"
    assert p1.git.exists(f"src/api/{ticket['id']}.ts", branch) or p1.git.exists(
        f"prisma/schema.prisma", branch
    )
    assert p1.git.exists(f"requirements/{rid}/build/ci.yml", f"build/{rid}")
    assert p1.git.exists(f"app/{rid}/server.js", f"build/{rid}")
    assert p1.git.exists(f"app/{rid}/prisma/schema.prisma", f"build/{rid}")
    assert p1.git.exists(f"app/{rid}/public/index.html", f"build/{rid}")
    schema = p1.git.read(f"app/{rid}/prisma/schema.prisma", f"build/{rid}")
    assert "model " in schema
    html = p1.git.read(f"app/{rid}/public/index.html", f"build/{rid}")
    assert "function " in html


def test_gate_5_merges_feature_branches(tmp_path: Path):
    p1 = Phase1(tmp_path)
    rid = reach_plan(p1)
    sign_gate_4(p1, rid)
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
