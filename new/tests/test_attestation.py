"""The gate attestation predicate.

SoW 7.0 asks for an immutable trail capturing actor, timestamp, model and model
version, prompt version, artefact version and decision. These tests defend the
property that makes it evidence rather than a record: it cannot be produced
incomplete, and the bytes that were signed can be reproduced exactly.
"""

from __future__ import annotations

import json

import pytest

import attestation
from attestation import ContextInputs, IncompleteAttestation, build

COMPLETE = dict(
    requirement_id="REQ-0250",
    gate=1,
    decision="approve",
    actor="u-po",
    artefact_sha="9f2c1ab",
    model="anthropic/claude-haiku-4.5",
    model_version="2026-05-01",
    prompt_version=3,
)


class TestItRefusesToBeIncomplete:
    @pytest.mark.parametrize("missing", attestation.REQUIRED)
    def test_every_required_field_is_actually_required(self, missing):
        """A partial attestation carries the shape of evidence without the
        substance, and the gap is invisible exactly when it matters."""
        payload = dict(COMPLETE)
        payload[missing] = ""
        with pytest.raises(IncompleteAttestation, match=missing):
            build(**payload)

    def test_the_refusal_names_every_missing_field_at_once(self):
        """Reporting them one at a time turns one fix into several rounds."""
        payload = dict(COMPLETE, model="", actor="")
        with pytest.raises(IncompleteAttestation) as exc:
            build(**payload)
        assert "model" in str(exc.value) and "actor" in str(exc.value)

    def test_there_is_no_unknown_placeholder(self):
        """An attestation reading 'model: unknown' passes a schema check and
        answers no audit question."""
        with pytest.raises(IncompleteAttestation):
            build(**dict(COMPLETE, model_version="   "))


class TestTheStatementShape:
    def test_it_is_an_in_toto_statement(self):
        s = build(**COMPLETE)
        assert s["_type"] == "https://in-toto.io/Statement/v1"
        assert s["predicateType"].endswith("/GateApproval/v1")

    def test_the_subject_digest_is_the_commit(self):
        """Git already versions the artefact. A second counter would create two
        answers to 'which version was approved'."""
        s = build(**COMPLETE)
        assert s["subject"][0]["digest"] == {"gitCommit": "9f2c1ab"}

    def test_the_subject_names_the_gate_it_attests(self):
        assert build(**COMPLETE)["subject"][0]["name"] == "REQ-0250/gate-1"

    def test_it_carries_every_sow_audit_field(self):
        p = build(**COMPLETE)["predicate"]
        assert p["actor"] == "u-po"
        assert p["decision"] == "approve"
        assert p["timestamp"].endswith("Z")
        assert p["producedBy"]["model"] == "anthropic/claude-haiku-4.5"
        assert p["producedBy"]["modelVersion"] == "2026-05-01"
        assert p["producedBy"]["promptVersion"] == "3"

    def test_a_refusal_is_attested_the_same_as_an_approval(self):
        """'Who tried to approve what, and why it was rejected' is part of the
        trail, not an error to swallow."""
        s = build(**dict(COMPLETE, decision="discard"))
        assert s["predicate"]["decision"] == "discard"


class TestContextInputs:
    def test_contamination_is_answerable_from_the_record(self):
        """§13: 'was this artefact contaminated?' answerable for any artefact
        ever produced, not only the ones someone thought to test."""
        ctx = ContextInputs(
            skill_file_versions={"intake": 4, "design_system": 2},
            retrieval_document_ids=["doc-9", "doc-1"],
            precedent_enabled=False,
            connectors=["tracker", "docs"],
        )
        c = build(**COMPLETE, context=ctx)["predicate"]["contextInputs"]
        assert c["precedentEnabled"] is False
        assert c["skillFileVersions"] == {"design_system": 2, "intake": 4}
        assert c["retrievalDocumentIds"] == ["doc-1", "doc-9"]
        assert c["connectors"] == ["docs", "tracker"]

    def test_the_absence_of_context_is_recorded_not_omitted(self):
        """An empty list states 'nothing was retrieved'. A missing key states
        nothing at all, and the two must not look alike."""
        c = build(**COMPLETE)["predicate"]["contextInputs"]
        assert c["retrievalDocumentIds"] == []
        assert c["precedentEnabled"] is False

    def test_collections_are_ordered_so_the_signature_is_stable(self):
        a = build(**COMPLETE, context=ContextInputs(connectors=["b", "a"]))
        b = build(**COMPLETE, context=ContextInputs(connectors=["a", "b"]))
        assert a["predicate"]["contextInputs"] == b["predicate"]["contextInputs"]


class TestCanonicalBytes:
    def test_the_same_statement_serialises_identically(self):
        """A signature is over bytes. A statement re-serialised in a different
        key order verifies as tampered."""
        s = build(**COMPLETE, timestamp="2026-09-10T09:00:00Z")
        assert attestation.canonical(s) == attestation.canonical(json.loads(attestation.canonical(s)))

    def test_key_order_does_not_change_the_bytes(self):
        s = build(**COMPLETE, timestamp="2026-09-10T09:00:00Z")
        reordered = json.loads(json.dumps(dict(reversed(list(s.items())))))
        assert attestation.canonical(s) == attestation.canonical(reordered)

    def test_a_changed_field_changes_the_bytes(self):
        a = build(**COMPLETE, timestamp="2026-09-10T09:00:00Z")
        b = build(**dict(COMPLETE, actor="someone-else"), timestamp="2026-09-10T09:00:00Z")
        assert attestation.canonical(a) != attestation.canonical(b)


class TestWhereItIsCommitted:
    def test_it_travels_with_the_artefact_it_attests(self):
        """Same history as the artefact, rather than a separate store that can
        drift from it."""
        assert attestation.path_for("REQ-0250", 3, 1) == (
            "requirements/REQ-0250/attestations/gate-3.01.intoto.json"
        )

    def test_a_second_decision_at_the_same_gate_gets_its_own_file(self):
        """A revision overwritten by the later approval is the record an
        auditor asking 'why was this rewritten?' needs."""
        first = attestation.path_for("REQ-0250", 2, 1)
        second = attestation.path_for("REQ-0250", 2, 2)
        assert first != second
        assert first.endswith("gate-2.01.intoto.json")
        assert second.endswith("gate-2.02.intoto.json")


class TestSigning:
    def test_a_tampered_statement_does_not_verify(self):
        secret = b"test-key"
        statement = build(**COMPLETE, timestamp="2026-09-10T09:00:00Z")
        envelope = attestation.sign(statement, secret)
        envelope["payload"]["predicate"]["actor"] = "someone-else"
        with pytest.raises(attestation.SignatureMismatch):
            attestation.verify(envelope, secret)

    def test_the_original_verifies_and_returns_the_statement(self):
        secret = b"test-key"
        statement = build(**COMPLETE, timestamp="2026-09-10T09:00:00Z")
        envelope = attestation.sign(statement, secret)
        assert attestation.verify(envelope, secret)["predicate"]["actor"] == "u-po"
