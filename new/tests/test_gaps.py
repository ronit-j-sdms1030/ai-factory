"""Factory-owned architecture gaps — local substitutes, not tenant vendors."""

from __future__ import annotations

from pathlib import Path

import connectors
from phase1 import directory, prompt_registry, usage_ledger
from phase1.platform import Phase1
from phase4 import context, findings, product
from tests.test_phase3 import reach_plan, sign_gate_4


def test_repomix_budget_is_an_input():
    packed = context.pack({"a.ts": "x" * 40, "b.ts": "y" * 80}, budget=20)
    assert packed["tool"] == "repomix"
    assert packed["status"] == "substitute"
    assert packed["used"] <= packed["budget"]
    assert packed["omitted"] == ["b.ts"]
    over = context.pack({"big.ts": "z" * 200}, budget=10)
    assert over["ok"] is False


def test_codegraph_and_serena_stay_local():
    files = {
        "src/api/x.ts": "export function hello() { return 1 }\nexport function other() { return 2 }\n"
    }
    graph = context.graph(files)
    assert graph["tenant_local"] is True
    assert {row["symbol"] for row in graph["nodes"]} >= {"hello", "other"}
    edited = context.edit_symbol(files, "hello", "export function hello() { return 9 }")
    assert edited["ok"] is True
    assert "return 9" in edited["files"]["src/api/x.ts"]
    assert "function other" in edited["files"]["src/api/x.ts"]


def test_acp_marks_engine_swappable():
    row = context.acp("agentless")
    assert row["protocol"] == "acp"
    assert row["swappable"] is True


def test_connector_requires_client_id_and_blocks_unknown_host():
    try:
        connectors.resolve("tracker", client_id="")
    except connectors.ConnectorRefused as exc:
        assert "client_id" in str(exc)
    else:
        raise AssertionError("expected ConnectorRefused")
    doc = connectors.resolve_all("demo")
    assert set(doc["resolved"]) == set(connectors.ROLES)
    assert connectors.sni_allow("unpkg.com", client_id="demo") is True
    assert connectors.sni_allow("evil.example", client_id="demo") is False
    try:
        connectors.call("docs", "write", {}, client_id="demo", write=True)
    except connectors.ConnectorRefused:
        pass
    else:
        raise AssertionError("docs is read-only")


def test_defectdojo_dedups_and_blocks_high():
    board = findings.ingest(
        [
            {"tool": "gitleaks", "rule": "secret", "path": "a.ts", "severity": "high"},
            {"tool": "gitleaks", "rule": "secret", "path": "a.ts", "severity": "high"},
            {"tool": "bandit", "rule": "exec", "path": "b.py", "severity": "low"},
        ],
        loc=50,
    )
    assert board["tool"] == "defectdojo"
    assert board["ok"] is False
    assert board["duplicates_dropped"] == 1
    assert len(board["findings"]) == 2
    assert board["density_per_kloc"] > 0


def test_prompt_registry_versions_and_records_approver(tmp_path: Path):
    rows = prompt_registry.inventory(tmp_path)
    assert {row["name"] for row in rows} >= {"intake", "brd", "architect"}
    assert all(str(row["version"]).startswith("sha256:") for row in rows)
    recorded = prompt_registry.record(tmp_path, "intake", "u-po")
    assert recorded["approver"] == "u-po"
    hist = prompt_registry.history(tmp_path, "intake")
    assert hist[0]["approver"] == "u-po"


def test_assemble_writes_unit_and_property_tests():
    files = product.assemble(
        "REQ-0099",
        brd_text="- **Room:** id, name\n",
        screens=[{"name": "BookRoom", "source": "function BookRoom() { return <main /> }\n"}],
    )
    assert "app/REQ-0099/src/api/entities.test.js" in files
    assert "app/REQ-0099/src/ai/infer.test.js" in files
    assert "app/REQ-0099/tests/property/entities.property.test.js" in files
    assert "node --test" in files["app/REQ-0099/package.json"]


def test_gate_4_records_context_findings_and_app_tests(tmp_path: Path):
    p1 = Phase1(tmp_path)
    rid = reach_plan(p1)
    last = sign_gate_4(p1, rid)
    assert last["build"]["context"]["pack"]["budget"] == context.TOKEN_BUDGET
    assert last["build"]["findings"]["tool"] == "defectdojo"
    assert last["build"]["sandbox"]["egress"]["default"] == "block"
    assert p1.git.exists(f"app/{rid}/src/api/entities.test.js", f"build/{rid}")
    assert p1.git.exists(f"requirements/{rid}/build/CONTEXT.md", f"build/{rid}")
    assert last.get("connectors") or p1.get(rid).get("connectors")


def test_usage_dashboard_exposes_governance(tmp_path: Path):
    p1 = Phase1(tmp_path)
    board = usage_ledger.dashboard(tmp_path, store=p1.store)
    assert board["governance"]["status"] == "substitute"
    assert "gateAcceptRate" in board["governance"]


def test_change_request_goes_through_connector(tmp_path: Path):
    p1 = Phase1(tmp_path)
    rid = reach_plan(p1)
    sign_gate_4(p1, rid)
    p1.decide(rid, directory.actor("u-se"), "approve")
    p1.decide(rid, directory.actor("u-requester"), "approve")
    p1.decide(rid, directory.actor("u-rm"), "approve")
    cr = p1.submit_change_request(
        directory.actor("u-requester"), rid, "latency", "timeout"
    )
    assert cr["connector"]["role"] == "change_mgmt"
    assert cr["connector"]["status"] == "substitute"
