"""Screen-to-page coverage before a reviewer sees anything.

A page with no screen is a gap and is filled deterministically from the page's
own name. A screen tracing to no page is reported, not deleted.
"""

from __future__ import annotations

import re
from typing import Any


HEADING = re.compile(r"^###\s+(\S+)\s*$", re.M)
PAGE_LINE = re.compile(r"^[-*]\s+\*?\*?(.+?)\*?\*?\s*:\s*(.+)$", re.M)
PAGE_HEADING = re.compile(r"^##(?:\s+\d+\.)?\s+Page behaviour\b", re.M | re.I)


def _fold(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def pages_from_brd(brd_text: str) -> list[dict[str, str]]:
    pages: list[dict[str, str]] = []
    match = PAGE_HEADING.search(brd_text)
    if match:
        body = brd_text[match.end() :].split("\n## ", 1)[0]
        for line in PAGE_LINE.finditer(body):
            pages.append({"id": line.group(1).strip(), "description": line.group(2).strip()})
    if pages:
        return pages
    for match in HEADING.finditer(brd_text):
        ident = match.group(1)
        pages.append({"id": ident, "description": ident})
    if not pages:
        pages.append({"id": "Primary", "description": "Primary capability from the BRD"})
    return pages


def matches(page: dict[str, str], screen: dict[str, Any]) -> bool:
    page_id = _fold(page.get("id") or "")
    screen_name = _fold(str(screen.get("name") or ""))
    if page_id and screen_name and (page_id == screen_name or page_id in screen_name or screen_name in page_id):
        return True
    page_key = _fold(page["id"] + " " + page.get("description", ""))
    screen_key = _fold(str(screen.get("name") or "") + " " + str(screen.get("route") or ""))
    if not page_key or not screen_key:
        return False
    return page_key in screen_key or screen_key in page_key


def check(pages: list[dict[str, str]], screens: list[dict[str, Any]]) -> dict[str, Any]:
    gaps = [page for page in pages if not any(matches(page, screen) for screen in screens)]
    extras = [
        screen
        for screen in screens
        if not any(matches(page, screen) for page in pages)
    ]
    return {"gaps": gaps, "extras": extras}


def component_name(page: dict[str, str], used: set[str]) -> str:
    raw = page["id"] if re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", page["id"]) else (
        page["id"] + " " + page.get("description", "")
    )
    words = re.findall(r"[A-Za-z0-9]+", raw)
    name = "".join(word[:1].upper() + word[1:] for word in words) or "Screen"
    if name[0].isdigit():
        name = "R" + name
    candidate = name
    n = 2
    while candidate in used:
        candidate = f"{name}{n}"
        n += 1
    used.add(candidate)
    return candidate
