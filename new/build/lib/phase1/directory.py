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
        "roles": ["business_analyst"],
        "team": "delivery",
        "email": "requester@client.example",
    },
    "u-po": {
        "id": "u-po",
        "name": "Product Owner",
        "roles": ["product_owner"],
        "team": "product",
        "email": "po@client.example",
    },
    "u-bo": {
        "id": "u-bo",
        "name": "Business Owner",
        "roles": ["business_owner"],
        "team": "business",
        "email": "bo@client.example",
    },
    "u-ctl": {
        "id": "u-ctl",
        "name": "Client Tech Lead",
        "roles": ["client_tech_lead"],
        "team": "client",
        "email": "ctl@client.example",
    },
}


def actor(actor_id: str) -> dict[str, Any]:
    if actor_id not in DIRECTORY:
        raise KeyError(f"unknown identity {actor_id}")
    return dict(DIRECTORY[actor_id])


def freeze_chain(template: str) -> dict[str, list[dict[str, Any]]]:
    """Named people for each gate this template fires, resolved once."""
    gates = list(workflow_templates.for_name(template).gates)
    roles = gate_engine.resolve_approval_chain({}, gates)
    people = list(DIRECTORY.values())
    frozen: dict[str, list[dict[str, Any]]] = {}
    for gate, needed in roles.items():
        frozen[gate] = []
        for role in needed:
            match = next((dict(p) for p in people if role in p["roles"]), None)
            if match:
                frozen[gate].append(match)
    return frozen
