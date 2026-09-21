"""OWASP ZAP — binary if present; HTML snapshot DAST otherwise."""

from __future__ import annotations

from typing import Any
from urllib import error, request

import substitutes


def scan(preview_url: str, html: str = "") -> dict[str, Any]:
    found = substitutes.binary("zap")
    snapshot = html
    if not snapshot and preview_url.startswith(("http://", "https://")):
        snapshot = _fetch(preview_url)
    findings = substitutes.dast_html(snapshot, preview_url)
    blocking = [row for row in findings if row["severity"] in {"critical", "high"}]
    if found:
        return {
            "tool": "owasp-zap",
            "status": "available",
            "reason": "",
            "target": preview_url,
            "binary": found,
            "findings": findings,
            "ok": not blocking,
        }
    return {
        "tool": "owasp-zap",
        "status": "substitute",
        "reason": "ZAP missing; snapshot header/sink scan ran",
        "target": preview_url,
        "findings": findings,
        "ok": not blocking,
    }


def _fetch(url: str) -> str:
    try:
        with request.urlopen(url, timeout=2) as response:
            return response.read().decode("utf-8", errors="replace")
    except (error.URLError, TimeoutError, ValueError):
        return ""
