"""The gate attestation — signed evidence that a decision was taken.

SoW 7.0 asks for an immutable audit trail capturing actor, timestamp, model and
model *version*, prompt version, artefact version and decision. No product ships
that as a table, because it is a claim about a specific pipeline: which agent
produced the artefact, under which instructions, and who approved it.

**Why a signed predicate rather than a database row.** A row in a table the
platform owns is evidence only to someone who trusts the platform's operator not
to have edited it. An in-toto statement signed at the moment of decision is
evidence to anyone holding the public key, including an auditor who trusts
nobody. The difference matters precisely when it is being questioned.

**Why it refuses to be built incomplete.** A partial attestation is worse than
none: it carries the shape of evidence without the substance, and the gap is
invisible at the point it matters. So a missing required field raises rather
than defaulting, and there is no "unknown" sentinel for the model or the actor.

**The artefact version is the commit SHA.** Git already versions the artefact;
adding a second counter would create two answers to "which version was
approved". The digest names a `gitCommit`, which is what in-toto expects for
source-controlled subjects.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
PREDICATE_TYPE = "https://starkdigital.in/attestations/GateApproval/v1"

# SoW 7.0's audit fields. Named here rather than checked inline so the
# requirement is legible as a list, and so a field cannot be dropped by editing
# one branch of a conditional.
REQUIRED = (
    "actor",
    "decision",
    "gate",
    "requirement_id",
    "artefact_sha",
    "model",
    "model_version",
    "prompt_version",
)


class IncompleteAttestation(Exception):
    """A predicate missing a field SoW 7.0 requires.

    Raised rather than filled with a placeholder. An attestation reading
    "model: unknown" would satisfy a schema check and answer no audit question,
    which is the failure this whole component exists to prevent.
    """


@dataclass
class ContextInputs:
    """What fed the artefact, so contamination is answerable from evidence.

    §13 makes this the difference between *"we believe runs are isolated"* and
    *"was this artefact contaminated?"* being answerable for any artefact ever
    produced, rather than only the ones someone thought to test.
    """

    skill_file_versions: dict[str, str | int] = field(default_factory=dict)
    retrieval_document_ids: list[str] = field(default_factory=list)
    precedent_enabled: bool = False
    connectors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "skillFileVersions": dict(sorted(self.skill_file_versions.items())),
            "retrievalDocumentIds": sorted(self.retrieval_document_ids),
            "precedentEnabled": self.precedent_enabled,
            "connectors": sorted(self.connectors),
        }


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build(
    *,
    requirement_id: str,
    gate: int,
    decision: str,
    actor: str,
    artefact_sha: str,
    model: str,
    model_version: str,
    prompt_version: str | int,
    context: ContextInputs | None = None,
    template: str = "",
    timestamp: str | None = None,
) -> dict[str, Any]:
    """An in-toto statement for one gate decision.

    ``timestamp`` is injectable so a test can assert on a fixed value; it
    defaults to now, and nothing else in this module reads a clock.
    """
    values = {
        "actor": actor,
        "decision": decision,
        "gate": gate,
        "requirement_id": requirement_id,
        "artefact_sha": artefact_sha,
        "model": model,
        "model_version": model_version,
        "prompt_version": prompt_version,
    }
    missing = [k for k in REQUIRED if not str(values.get(k) or "").strip()]
    if missing:
        raise IncompleteAttestation(
            "cannot attest without " + ", ".join(missing) + " — a partial attestation "
            "carries the shape of evidence without the substance"
        )

    ctx = context or ContextInputs()
    return {
        "_type": STATEMENT_TYPE,
        "subject": [
            {
                "name": f"{requirement_id}/gate-{gate}",
                "digest": {"gitCommit": artefact_sha},
            }
        ],
        "predicateType": PREDICATE_TYPE,
        "predicate": {
            "requirementId": requirement_id,
            "gate": gate,
            "decision": decision,
            "actor": actor,
            "timestamp": timestamp or _utc_now(),
            "workflowTemplate": template,
            "producedBy": {
                "model": model,
                "modelVersion": model_version,
                "promptVersion": str(prompt_version),
            },
            "contextInputs": ctx.as_dict(),
        },
    }


def canonical(statement: dict[str, Any]) -> str:
    """The exact bytes to sign, and to verify against later.

    Sorted keys and no incidental whitespace, because a signature is over bytes:
    a statement re-serialised with a different key order verifies as tampered.
    Signing whatever `json.dumps` happened to produce would make verification
    depend on the version of the library that wrote it.
    """
    return json.dumps(statement, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def path_for(requirement_id: str, gate: int, sequence: int = 1) -> str:
    """Where the signed predicate is committed.

    One file per *decision*, not per gate. A gate can produce an approval, a
    revision, then two approvals on the reworked document; writing them all to
    ``gate-2.intoto.json`` would keep only the last, and the record of the
    revision is exactly what an auditor asking "why was this rewritten?" needs.

    ``sequence`` is 1-based among decisions at that gate for this requirement.
    """
    if sequence < 1:
        raise ValueError("attestation sequence is 1-based; a zero would collide with nothing and look like a counter")
    return (
        f"requirements/{requirement_id}/attestations/"
        f"gate-{gate}.{sequence:02d}.intoto.json"
    )


class SignatureMismatch(Exception):
    """The envelope does not match the key, or is not in the signed shape."""


def sign(statement: dict[str, Any], secret: bytes, *, keyid: str = "dev") -> dict[str, Any]:
    """Wrap a statement in a signature envelope.

    Production uses Sigstore keyless signing. This is the local stand-in: HMAC
    over the canonical bytes, so verification does not depend on JSON key order.
    The envelope is what gets committed; the statement inside is what SoW 7.0
    asks for.
    """
    payload = canonical(statement).encode("utf-8")
    return {
        "payloadType": STATEMENT_TYPE,
        "payload": statement,
        "signatures": [
            {
                "keyid": keyid,
                "alg": "hmac-sha256",
                "sig": hmac.new(secret, payload, hashlib.sha256).hexdigest(),
            }
        ],
    }


def verify(envelope: dict[str, Any], secret: bytes) -> dict[str, Any]:
    """Return the statement if the envelope matches ``secret``.

    Raises rather than returning false: a caller that forgets to check a
    boolean would treat a forged attestation as a real one.
    """
    if not envelope or envelope.get("payloadType") != STATEMENT_TYPE:
        raise SignatureMismatch("not a signed in-toto envelope")
    statement = envelope.get("payload")
    if not isinstance(statement, dict):
        raise SignatureMismatch("envelope has no statement payload")
    sigs = envelope.get("signatures") or []
    expected = hmac.new(secret, canonical(statement).encode("utf-8"), hashlib.sha256).hexdigest()
    for sig in sigs:
        if hmac.compare_digest(str(sig.get("sig") or ""), expected):
            return statement
    raise SignatureMismatch("attestation signature does not match")
