"""Optional SWE-agent under a turn cap. Unset SWE_AGENT keeps the local stub."""

from __future__ import annotations

import json
import os
from typing import Any
from urllib import error, request


def apply(ticket: dict[str, Any], files: dict[str, str], env: dict[str, str] | None = None) -> dict[str, Any]:
    values = env if env is not None else os.environ
    url = (values.get("SWE_AGENT") or values.get("SWE_AGENT_URL") or "").rstrip("/")
    if not url:
        return {
            "engine": "swe-agent",
            "status": "substitute",
            "reason": "SWE_AGENT unset; agentless stub used",
            "files": files,
        }
    payload = json.dumps({"ticket": ticket, "turn_cap": 8}).encode()
    req = request.Request(
        url + "/run",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=5) as response:
            body = json.load(response)
    except (error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return {"engine": "swe-agent", "status": "skipped", "reason": str(exc), "files": files}
    return {"engine": "swe-agent", "status": "keyed", "reason": "", "files": body.get("files") or files}
