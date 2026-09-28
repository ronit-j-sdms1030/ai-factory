"""MCP connector manifest + SNI egress. The plumbing is ours; vendors are not live.

Agents ask for a role (tracker, docs, design, messaging, change_mgmt).
The resolver maps that to this client's local substitute. Default egress is block-all.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

ROLES = ("tracker", "docs", "design", "messaging", "change_mgmt")

DEFAULT_MANIFEST: dict[str, Any] = {
    "client_id": "demo",
    "connectors": {
        "tracker": {
            "kind": "local",
            "name": "factory-tracker",
            "write": True,
            "host": "local.factory",
        },
        "docs": {
            "kind": "local",
            "name": "factory-docs",
            "write": False,
            "host": "local.factory",
        },
        "design": {
            "kind": "local",
            "name": "factory-design",
            "write": False,
            "host": "local.factory",
        },
        "messaging": {
            "kind": "local",
            "name": "factory-inbox",
            "write": True,
            "host": "local.factory",
        },
        "change_mgmt": {
            "kind": "local",
            "name": "manageengine-substitute",
            "write": True,
            "host": "local.factory",
            "note": "SoW 20.0 field-aligned; client ManageEngine is not live",
        },
    },
    "egress": {
        "default": "block",
        "allow": ("local.factory", "unpkg.com", "127.0.0.1", "localhost"),
    },
}


class ConnectorRefused(Exception):
    """Unknown role, missing client_id, write denied, or SNI blocked."""


def manifest_for(client_id: str) -> dict[str, Any]:
    if not (client_id or "").strip():
        raise ConnectorRefused("client_id is required")
    doc = {
        "client_id": client_id,
        "connectors": dict(DEFAULT_MANIFEST["connectors"]),
        "egress": {
            "default": DEFAULT_MANIFEST["egress"]["default"],
            "allow": list(DEFAULT_MANIFEST["egress"]["allow"]),
        },
        "status": "substitute",
        "note": "Jira / SharePoint / Teams / ManageEngine stay on the client tenant",
    }
    return doc


def resolve(role: str, *, client_id: str, manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    if not (client_id or "").strip():
        raise ConnectorRefused("client_id is required")
    if role not in ROLES:
        raise ConnectorRefused(f"unknown connector role {role!r}")
    doc = manifest or manifest_for(client_id)
    if doc.get("client_id") != client_id:
        raise ConnectorRefused("manifest client_id does not match the run")
    target = dict((doc.get("connectors") or {}).get(role) or {})
    if not target:
        raise ConnectorRefused(f"no connector for {role}")
    return {"role": role, "client_id": client_id, **target}


def resolve_all(client_id: str) -> dict[str, Any]:
    doc = manifest_for(client_id)
    return {
        "client_id": client_id,
        "status": doc["status"],
        "note": doc["note"],
        "resolved": {role: resolve(role, client_id=client_id, manifest=doc) for role in ROLES},
        "egress": doc["egress"],
    }


def sni_host(target: str) -> str:
    raw = (target or "").strip()
    if "://" in raw:
        return (urlparse(raw).hostname or "").lower()
    return raw.split("/")[0].split(":")[0].lower()


def sni_allow(host: str, *, client_id: str, manifest: dict[str, Any] | None = None) -> bool:
    doc = manifest or manifest_for(client_id)
    allowed = {item.lower() for item in (doc.get("egress") or {}).get("allow") or ()}
    name = sni_host(host)
    if not name:
        return False
    return name in allowed


def sni_policy(client_id: str = "demo") -> dict[str, Any]:
    doc = manifest_for(client_id)
    return {"default": "block", "allow": list(doc["egress"]["allow"]), "status": "substitute"}


def call(
    role: str,
    action: str,
    payload: dict[str, Any] | None = None,
    *,
    client_id: str,
    actor: dict[str, Any] | None = None,
    write: bool = False,
) -> dict[str, Any]:
    """One connector call — same shape a Temporal activity would record."""
    target = resolve(role, client_id=client_id)
    if write and not target.get("write"):
        raise ConnectorRefused(f"{role} is read-only for this client")
    host = str(target.get("host") or "")
    if host and not sni_allow(host, client_id=client_id):
        raise ConnectorRefused(f"SNI blocked {host}")
    return {
        "role": role,
        "action": action,
        "client_id": client_id,
        "connector": target.get("name"),
        "write": bool(write),
        "actor": (actor or {}).get("id"),
        "payload": dict(payload or {}),
        "status": "substitute",
        "activity": "connector_call",
    }
