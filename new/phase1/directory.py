"""Named approvers resolved once from the originator's directory.

Entra groups are the production source. This directory is the seam: freeze
identities at intake, never look them up again per gate.
"""

from __future__ import annotations

from typing import Any

import gate_engine
import workflow_templates


# Demo identities — replace with Entra SCIM. Roles match gate_engine.GATES.
DIRECTORY: dict[str, dict[str, Any]] = {
    "u-requester": {
        "id": "u-requester",
        "name": "Requester",
        "roles": ["business_analyst", "business_stakeholder"],
        "team": "delivery",
        "email": "requester@client.example",
        "github_login": "u-requester",
    },
    "u-po": {
        "id": "u-po",
        "name": "Product Owner",
        "roles": ["product_owner"],
        "team": "product",
        "email": "po@client.example",
        "github_login": "u-po",
    },
    "u-bo": {
        "id": "u-bo",
        "name": "Business Owner",
        "roles": ["business_owner"],
        "team": "business",
        "email": "bo@client.example",
        "github_login": "u-bo",
    },
    "u-ctl": {
        "id": "u-ctl",
        "name": "Client Tech Lead",
        "roles": ["client_tech_lead"],
        "team": "client",
        "email": "ctl@client.example",
        "github_login": "u-ctl",
    },
    "u-arch": {
        "id": "u-arch",
        "name": "Architect",
        "roles": ["architect"],
        "team": "architecture",
        "email": "architect@client.example",
        "github_login": "u-arch",
    },
    "u-ux": {
        "id": "u-ux",
        "name": "UI/UX",
        "roles": ["ui_ux"],
        "team": "design",
        "email": "ux@client.example",
        "github_login": "u-ux",
    },
    "u-ba": {
        "id": "u-ba",
        "name": "Business Analyst",
        "roles": ["business_analyst"],
        "team": "product",
        "email": "ba@client.example",
        "github_login": "u-ba",
    },
    "u-tl": {
        "id": "u-tl",
        "name": "Tech Lead",
        "roles": ["tech_lead", "stream_lead"],
        "team": "development",
        "email": "techlead@client.example",
        "github_login": "u-tl",
    },
    "u-sl-qa": {
        "id": "u-sl-qa",
        "name": "QA Stream Lead",
        "roles": ["stream_lead"],
        "team": "qa",
        "email": "qa-lead@client.example",
        "github_login": "u-sl-qa",
    },
    "u-se": {
        "id": "u-se",
        "name": "Senior Engineer",
        "roles": ["senior_engineer"],
        "team": "development",
        "email": "senior@client.example",
        "github_login": "u-se",
    },
    "u-rm": {
        "id": "u-rm",
        "name": "Release Manager",
        "roles": ["release_manager"],
        "team": "release",
        "email": "release@client.example",
        "github_login": "u-rm",
    },
}


def actor(actor_id: str) -> dict[str, Any]:
    if actor_id in DIRECTORY:
        return dict(DIRECTORY[actor_id])
    lowered = actor_id.lower()
    for person in DIRECTORY.values():
        if person.get("github_login", "").lower() == lowered:
            return dict(person)
        if person.get("email", "").lower() == lowered:
            return dict(person)
    raise KeyError(f"unknown identity {actor_id}")


def freeze_chain(
    template: str, originator_id: str | None = None
) -> dict[str, list[dict[str, Any]]]:
    """Named people for each gate this template fires, resolved once."""
    gates = list(workflow_templates.for_name(template).gates)
    roles = gate_engine.resolve_approval_chain({}, gates)
    people = list(DIRECTORY.values())
    frozen: dict[str, list[dict[str, Any]]] = {}
    originator_id = originator_id or ""
    definition = workflow_templates.for_name(template)
    for gate, needed in roles.items():
        spec = gate_engine.GATES[int(gate)]
        frozen[gate] = []
        seen: set[str] = set()
        for role in needed:
            candidates = [dict(p) for p in people if role in p["roles"]]
            if spec.excludes_originator and originator_id:
                skipped = [p for p in candidates if p["id"] != originator_id]
                if skipped:
                    candidates = skipped
            if role == "stream_lead" and definition.dev_streams != "single":
                chosen = candidates
            elif candidates:
                chosen = candidates[:1]
            else:
                chosen = []
            for person in chosen:
                if person["id"] in seen:
                    continue
                seen.add(person["id"])
                frozen[gate].append(person)
    return frozen
