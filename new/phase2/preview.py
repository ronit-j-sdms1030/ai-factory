"""Unique Gate 3 / UAT preview — the approved screens, live, against the product API."""

from __future__ import annotations

from typing import Any

from phase4 import product


def url(requirement_id: str) -> str:
    return f"/preview/{requirement_id}"


def host() -> dict[str, str]:
    """Factory-served preview. E2B cloud is not live on this demo path."""
    return {"sandbox": "factory", "status": "substitute", "tool": "E2B cloud"}


def document(
    requirement_id: str,
    screens: list[dict[str, Any]],
    brd_text: str = "",
) -> str:
    entities = product.parse_entities(brd_text) if brd_text else []
    return product.frontend_html(
        requirement_id,
        screens,
        f"/preview/{requirement_id}/api",
        entities=entities,
    )
