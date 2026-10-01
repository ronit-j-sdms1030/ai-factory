"""Which screens and records each BRD requirement is about.

The plan, the tests and every review check read the same mapping, so a
requirement cannot be "covered" in one place and missing in another.
"""

from __future__ import annotations

import re

import department_routing as routing
from phase2 import architect
from phase2 import contract
from phase2.coverage import _fold, pages_from_brd

_LOOSE_HEAD = re.compile(r"^###\s+(\S+)(?:\s+[—–-]\s+(.+))?\s*$", re.M)
_AI_WORDS = re.compile(
    r"\bai\b|\bllm\b|machine learning|language model|recommend|suggestion|predict|classif|chatbot|assistant",
    re.I,
)
_AUTH_RECORD = ("user", "staff", "account", "member", "operator", "login")


_NEXT_SECTION = re.compile(r"^##\s", re.M)
_CRITERIA = re.compile(r"\*{0,2}Acceptance criteria", re.I)
_GENERIC_FIELDS = {"id", "status", "name", "action", "at", "created_at", "updated_at", "type"}


def requirements(brd_text: str) -> list[dict[str, str]]:
    found = contract._brd_requirements(brd_text or "")
    if not found:
        return [
            {"id": m.group(1), "title": (m.group(2) or m.group(1)).strip(), "body": "", "criteria": ""}
            for m in _LOOSE_HEAD.finditer(brd_text or "")
        ]
    rows = []
    for row in found:
        # The last requirement otherwise runs on into Page behaviour and the data model.
        body = _NEXT_SECTION.split(row["body"], maxsplit=1)[0].strip()
        criteria = _NEXT_SECTION.split(row.get("criteria") or "", maxsplit=1)[0].strip()
        rows.append({**row, "body": body, "criteria": criteria})
    return rows


def statement(requirement: dict[str, str]) -> str:
    """What the requirement asks for, without the shared Given/When/Then boilerplate."""
    body = _CRITERIA.split(str(requirement.get("body") or ""), maxsplit=1)[0]
    return f"{requirement.get('title') or ''}\n{body}"


def entities(brd_text: str, architecture_text: str = "") -> list[tuple[str, list[str]]]:
    rows = architect._entities((architecture_text or "") + "\n" + (brd_text or ""))
    names, _ = routing.repair_entities([name for name, _fields in rows])
    by_key = {
        routing.fold_entity(name): [re.sub(r"__owner_\w+$", "", str(f)) for f in fields]
        for name, fields in rows
    }
    return [(name, by_key.get(routing.fold_entity(name), [])) for name in names]


def pages(brd_text: str) -> list[dict[str, str]]:
    return pages_from_brd(brd_text or "")


def _page_score(text: str, page: dict[str, str]) -> int:
    score = contract._overlap(
        contract._words(text), contract._words(f"{page.get('id') or ''} {page.get('description') or ''}")
    )
    ident = re.sub(r"[^a-z0-9]", "", str(page.get("id") or "").lower())
    if ident and ident in re.sub(r"[^a-z0-9]", "", text.lower()):
        score += 3
    return score


def pages_for(requirement: dict[str, str], brd_pages: list[dict[str, str]]) -> list[dict[str, str]]:
    """The screens this requirement is about: the best match on its own statement,
    falling back to its acceptance criteria when the statement names no screen."""
    full = f"{requirement.get('title') or ''}\n{requirement.get('body') or ''}"
    for text in (statement(requirement), full):
        scored = [(_page_score(text, page), page) for page in brd_pages]
        best = max((score for score, _page in scored), default=0)
        if best >= 3:
            return [page for score, page in scored if score == best]
    return []


def record_for_page(
    page: dict[str, str] | None, records: list[tuple[str, list[str]]]
) -> str:
    """The record a screen reads or writes. Sign-in screens use the login record."""
    if not page or not records:
        return ""
    rows = [{"name": name, "fields": fields} for name, fields in records]
    binding = contract.screen_contract(page, rows)
    if binding and binding.get("record"):
        return str(binding["record"])
    kind = contract._kind(str(page.get("id") or ""), str(page.get("description") or ""))
    if kind == "auth":
        for name, _fields in records:
            if any(token in name.lower() for token in _AUTH_RECORD):
                return name
        return ""
    page_key = _fold(page.get("id") or "")
    blob = str(page.get("description") or "").lower()
    for name, fields in records:
        key = _fold(name)
        if key and (key in page_key or page_key in key):
            return name
    best, score = "", 0
    for name, fields in records:
        hits = sum(
            1
            for field in fields
            if field not in {"id", "status"}
            and (field in blob or field.replace("_", " ") in blob)
        )
        if hits > score:
            best, score = name, hits
    return best


def records_for(
    requirement: dict[str, str],
    records: list[tuple[str, list[str]]],
    tied_pages: list[dict[str, str]],
) -> list[str]:
    blob = statement(requirement).lower()
    found: list[str] = []
    for page in tied_pages:
        name = record_for_page(page, records)
        if name and name not in found:
            found.append(name)
    for name, fields in records:
        if name in found:
            continue
        spaced = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name).lower()
        if spaced in blob or name.lower() in blob:
            found.append(name)
            continue
        if any(
            field not in _GENERIC_FIELDS
            and (field in blob or field.replace("_", " ") in blob)
            for field in fields
        ):
            found.append(name)
    return found


def screen_page(screen_name: str, brd_pages: list[dict[str, str]]) -> dict[str, str] | None:
    key = _fold(screen_name)
    for page in brd_pages:
        page_key = _fold(page.get("id") or "")
        if page_key and (page_key == key or re.sub(r"\d+$", "", key) == page_key):
            return page
    return None


def wants_ai(brd_text: str) -> bool:
    return bool(_AI_WORDS.search(brd_text or ""))


def shared_login(brd_text: str) -> bool:
    blob = (brd_text or "").lower()
    return any(
        token in blob
        for token in ("shared login", "shared account", "shared credential", "one login", "single shared")
    )
