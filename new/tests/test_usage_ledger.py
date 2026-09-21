from pathlib import Path

from phase1.adapters.model import DeterministicModelGateway
from phase1.adapters.protocols import ModelCompletion
from phase1 import usage_ledger


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
    row = usage_ledger.record(tmp_path, completion, [], "", agent="brd")
    assert row["prompt_tokens"] == 100
    assert row["total_tokens"] == 120
    assert row["estimated"] is False
    assert row["usd"] >= 0
