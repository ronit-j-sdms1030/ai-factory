"""HMAC webhooks — GitHub review and workspace inbox produce the same event."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
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
        parsed = {
            "requirement_id": data.get("requirement_id"),
            "gate": int(data.get("gate") or 0),
            "outcome": data.get("outcome") or "",
            "actor_id": data.get("actor_id") or "",
        }
    else:
        review = data.get("review") or {}
        pr = data.get("pull_request") or {}
        ref = (pr.get("head") or {}).get("ref") or ""
        match = re.search(r"REQ-[A-Za-z0-9-]+", ref)
        requirement_id = match.group(0).rsplit("-W", 1)[0] if match else ref.split("/", 1)[-1]
        state = (review.get("state") or "").lower()
        outcome = {"approved": "approve", "changes_requested": "revise"}.get(state, state)
        login = (review.get("user") or {}).get("login") or ""
        if ref.startswith("scope/"):
            gate = 1
        elif ref.startswith("brd/"):
            gate = 2
        elif ref.startswith("design/"):
            gate = 3
        elif ref.startswith("plan/"):
            gate = 4
        elif ref.startswith("feat/") or ref.startswith("build/"):
            gate = 5
        elif ref.startswith("uat/"):
            gate = 6
        elif ref.startswith("release/"):
            gate = 7
        else:
            gate = 0
        parsed = {
            "requirement_id": requirement_id,
            "gate": gate,
            "outcome": outcome,
            "actor_id": login,
        }
    if int(parsed.get("gate") or 0) not in range(1, 8) or not parsed.get("requirement_id"):
        raise WebhookRefused("webhook is not a gate event")
    return parsed
