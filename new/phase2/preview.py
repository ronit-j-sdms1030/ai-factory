"""Unique Gate 3 preview URL per requirement — local stand-in for an E2B VM."""

from __future__ import annotations

from html import escape
from typing import Any


def url(requirement_id: str) -> str:
    return f"/preview/{requirement_id}"


def document(requirement_id: str, screens: list[dict[str, Any]]) -> str:
    parts = [
        "<!doctype html>",
        f"<html lang='en'><head><meta charset='utf-8'><title>Preview {escape(requirement_id)}</title>",
        "<style>body{font-family:system-ui,sans-serif;margin:0;background:#0f1419;color:#e7ecf1}",
        "header{padding:1rem 1.5rem;border-bottom:1px solid #243041} section{padding:1.5rem}",
        "pre{white-space:pre-wrap;background:#161d27;padding:1rem;border-radius:8px}</style></head><body>",
        f"<header><p>Preview URL {escape(url(requirement_id))}</p>",
        f"<h1>{escape(requirement_id)}</h1></header>",
    ]
    if not screens:
        parts.append("<section><p>No screens yet.</p></section>")
    for screen in screens:
        name = escape(str(screen.get("name") or "Screen"))
        route = escape(str(screen.get("route") or "/"))
        source = escape(str(screen.get("source") or ""))
        parts.append(f"<section id='{name}'><h2>{name} <code>{route}</code></h2><pre>{source}</pre></section>")
    parts.append("</body></html>")
    return "\n".join(parts)
