"""Optional OpenHands headless SDK. Unset OPENHANDS_URL keeps the local stub."""

from __future__ import annotations

import json
import os
from typing import Any
from urllib import error, request


def apply(ticket: dict[str, Any], files: dict[str, str], env: dict[str, str] | None = None) -> dict[str, Any]:
    values = env if env is not None else os.environ
    url = (values.get("OPENHANDS_URL") or "").rstrip("/")
    if not url:
        return {
            "engine": "openhands",
            "status": "substitute",
            "reason": "OPENHANDS_URL unset; agentless stub used",
            "files": files,
        }
    payload = json.dumps({"ticket": ticket, "files": list(files)}).encode()
    req = request.Request(
        url + "/api/conversations",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=5) as response:
            body = json.load(response)
    except (error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return {"engine": "openhands", "status": "skipped", "reason": str(exc), "files": files}
    return {"engine": "openhands", "status": "keyed", "reason": "", "files": body.get("files") or files}
