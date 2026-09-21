"""Local-reference and OPA HTTP gate policy adapters."""

from __future__ import annotations

import json
from urllib import request

import gate_engine
from phase1.adapters.protocols import PolicyVerdict


class LocalGatePolicy:
    def evaluate(self, gate, outcome, actor, requirement, *, expected_gate):
        try:
            gate_engine.evaluate(
                gate, outcome, actor, requirement, expected_gate=expected_gate
            )
            return PolicyVerdict(True)
        except gate_engine.GateRefused as exc:
            return PolicyVerdict(False, str(exc))


class OPAGatePolicy:
    def __init__(
        self,
        base_url: str,
        *,
        decision_path: str = "/v1/data/governed_factory/gate/verdict",
        timeout: float = 5,
    ):
        self.url = base_url.rstrip("/") + decision_path
        self.timeout = timeout

    def evaluate(self, gate, outcome, actor, requirement, *, expected_gate):
        payload = {
            "input": {
                "gate": gate,
                "outcome": outcome,
                "actor": actor,
                "requirement": requirement,
                "expected_gate": expected_gate,
            }
        }
        req = request.Request(
            self.url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with request.urlopen(req, timeout=self.timeout) as response:
            result = json.load(response).get("result") or {}
        return PolicyVerdict(bool(result.get("allowed")), str(result.get("reason") or ""))
