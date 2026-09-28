from pathlib import Path

from phase1 import usage_ledger
from phase1.adapters.model import DeterministicModelGateway
from phase1.adapters.protocols import ModelCompletion
from phase1.platform import Phase1
from phase1 import directory
from tests.test_phase2 import reach_design
from tests.test_phase3 import reach_plan, sign_gate_4


def test_usage_ledger_records_and_totals(tmp_path: Path):
    gw = DeterministicModelGateway(root=tmp_path)
    gw.complete([{"role": "user", "content": "hello from the site cabin"}], skill="intake rules")
    board = usage_ledger.dashboard(tmp_path)
    assert board["recorded"]["calls"] == 1
    assert board["recorded"]["total_tokens"] > 0
    assert board["byModel"][0]["model"] == "deterministic/intake"
    assert board["cycleEstimate"]["currency"] == "USD"
    assert len(board["recent"]) == 1


def test_usage_uses_provider_token_counts(tmp_path: Path):
    completion = ModelCompletion(
        text="ok",
        model="openai/gpt-4o-mini",
        model_version="mini",
        prompt_version="intake.skill.md",
        metadata={"usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}},
    )
    row = usage_ledger.record(
        tmp_path, completion, [], "", agent="brd", requirement_id="REQ-0009"
    )
    assert row["prompt_tokens"] == 100
    assert row["total_tokens"] == 120
    assert row["estimated"] is False
    assert row["usd"] >= 0
    assert row["requirement_id"] == "REQ-0009"
    board = usage_ledger.dashboard(tmp_path)
    assert board["byAgent"][0]["agent"] == "brd"
    assert board["byRequirement"][0]["requirementId"] == "REQ-0009"
    assert board["history"][0]["requirement_id"] == "REQ-0009"


def test_gate_writes_record_brd_architect_and_ui_usage(tmp_path: Path):
    p1 = Phase1(tmp_path)
    rid = reach_design(p1)
    agents = {row["agent"] for row in usage_ledger.dashboard(tmp_path)["byAgent"]}
    assert {"brd", "architect", "ui"} <= agents
    req_rows = usage_ledger.dashboard(tmp_path)["byRequirement"]
    assert any(row["requirementId"] == rid for row in req_rows)


def test_later_gates_record_remaining_agent_usage(tmp_path: Path):
    p1 = Phase1(tmp_path)
    rid = reach_plan(p1)
    agents = {row["agent"] for row in usage_ledger.dashboard(tmp_path)["byAgent"]}
    assert {"decomposer", "qa", "devops", "overview"} <= agents
    sign_gate_4(p1, rid)
    agents = {row["agent"] for row in usage_ledger.dashboard(tmp_path)["byAgent"]}
    assert {"build", "review", "adversary"} <= agents
    p1.decide(rid, directory.actor("u-se"), "approve")
    p1.decide(rid, directory.actor("u-requester"), "approve")
    p1.decide(rid, directory.actor("u-rm"), "approve")
    agents = {row["agent"] for row in usage_ledger.dashboard(tmp_path)["byAgent"]}
    assert "monitor" in agents
