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
    """Screens named in ``## Page behaviour`` — one row per screen id.

    BRDs often repeat the same screen under each ``### REQ-…`` requirement.
    Those repeats are behaviours of the same page, not new screens, so they
    must not mint LoginScreen2 / LoanManagement3 stubs.
    """
    pages: list[dict[str, str]] = []
    match = PAGE_HEADING.search(brd_text)
    if match:
        body = brd_text[match.end() :]
        # Stop before the next ## section or the first ### requirement heading.
        body = re.split(r"\n##\s+", body, maxsplit=1)[0]
        body = re.split(r"\n###\s+", body, maxsplit=1)[0]
        seen: set[str] = set()
        for line in PAGE_LINE.finditer(body):
            ident = line.group(1).strip()
            key = _fold(ident)
            if not key or key in seen:
                continue
            seen.add(key)
            pages.append({"id": ident, "description": line.group(2).strip()})
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
    page_id = str(page.get("id") or "")
    folded_id = _fold(page_id)
    if folded_id:
        for existing in used:
            base = re.sub(r"\d+$", "", existing)
            if _fold(existing) == folded_id or _fold(base) == folded_id:
                return existing
    raw = page_id if re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", page_id) else (
        page_id + " " + page.get("description", "")
    )
    words = re.findall(r"[A-Za-z0-9]+", raw)
    name = "".join(word[:1].upper() + word[1:] for word in words) or "Screen"
    if name[0].isdigit():
        name = "R" + name
    folded = _fold(name)
    for existing in used:
        if _fold(existing) == folded or _fold(re.sub(r"\d+$", "", existing)) == folded:
            return existing
    used.add(name)
    return name
