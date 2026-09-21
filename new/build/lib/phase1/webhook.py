"""HMAC webhooks — GitHub review and workspace inbox produce the same event."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any


class WebhookRefused(Exception):
    """Signature missing or wrong. The approval does not count."""


def verify(body: bytes, header: str, secret: bytes) -> None:
    expected = "sha256=" + hmac.new(secret, body, hashlib.sha256).hexdigest()
    if not header or not hmac.compare_digest(header, expected):
        raise WebhookRefused("webhook HMAC does not match")


def parse_approval(body: bytes) -> dict[str, Any]:
    """Accept either our inbox payload or a GitHub pull_request_review."""
    data = json.loads(body.decode("utf-8"))
    if "requirement_id" in data:
        return data
    review = data.get("review") or {}
    pr = data.get("pull_request") or {}
    ref = (pr.get("head") or {}).get("ref") or ""
    requirement_id = ref.split("/", 1)[-1]
    state = (review.get("state") or "").lower()
    outcome = {"approved": "approve", "changes_requested": "revise"}.get(state, state)
    login = (review.get("user") or {}).get("login") or ""
    gate = 1 if ref.startswith("scope/") else 2 if ref.startswith("brd/") else 0
    return {
        "requirement_id": requirement_id,
        "gate": gate,
        "outcome": outcome,
        "actor_id": login,
    }
