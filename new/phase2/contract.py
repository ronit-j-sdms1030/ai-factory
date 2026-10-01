"""The one contract the architect locks and the UI is allowed to draw.

Screens come from the BRD page list the business analyst signs.
Records come from the BRD data model the architect locks.
A screen name, or the word PostgreSQL, is never a record.
"""

from __future__ import annotations

import re
from typing import Any

_TECH = {
    "postgresql",
    "postgres",
    "sqlite",
    "react",
    "express",
    "prisma",
    "nodejs",
    "python",
}
# Login and audit rows are not the form the product is about.
_NOT_DOMAIN = {"user", "auditentry", "importlog"}


def problems(brd_text: str, entities: list[dict[str, Any]] | None) -> list[str]:
    """Why this architecture cannot drive the UI. Empty means it can."""
    from phase2.architect import _entities
    from phase2.coverage import _fold, pages_from_brd

    expected = _entities(brd_text or "")
    page_ids = {_fold(page.get("id") or "") for page in pages_from_brd(brd_text or "")}
    got: dict[str, list[str]] = {}
    issues: list[str] = []
    for row in entities or []:
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        key = _fold(name)
        if key in page_ids:
            issues.append(f"{name} is a screen, not a record")
        if key in _TECH:
            issues.append(f"{name} is the database, not a record")
        got[name] = [str(field) for field in (row.get("fields") or [])]
    for name, fields in expected:
        have = got.get(name)
        if have is None:
            issues.append(f"missing record {name}")
            continue
        for field in fields:
            if field not in have:
                issues.append(f"{name} is missing {field}")
    if expected and not got and not issues:
        issues.append("architecture has no records")
    return issues


def screen_contract(
    page: dict[str, str], entities: list[dict[str, Any]] | None
) -> dict[str, Any] | None:
    """Fields a screen must show, taken from the locked record and the page line."""
    if not entities:
        return None
    description = str(page.get("description") or "")
    blob = f"{page.get('id') or ''} {description}".lower()
    kind = _kind(str(page.get("id") or ""), description)
    record = _record_for_page(page, entities, kind)
    labels = _stated_labels(description)
    if not labels and record is not None and kind != "auth":
        labels = [
            _label_from_field(field)
            for field in record.get("fields") or []
            if field not in {"id", "status"}
        ]
    if not labels:
        return None
    readonly = [label for label in labels if _is_readonly(description, label)]
    typed = [label for label in labels if label not in readonly]
    action = ""
    if "sign out" in blob or "sign-out" in blob:
        action = "Sign out"
    return {
        "kind": kind,
        "record": (record or {}).get("name") or "",
        "labels": labels,
        "typed": typed,
        "readonly": readonly,
        "action": action,
    }


def covers(source: str, binding: dict[str, Any] | None) -> bool:
    """A model screen is usable only when the locked labels are fields or columns.

    Mentioning the words in a paragraph is not enough. The business analyst
    releases these screens, so a guessed layout cannot pass on a name-drop.
    """
    if not binding:
        return True
    text = source or ""
    kind = str(binding.get("kind") or "")
    if kind == "list":
        labels = list(binding.get("labels") or [])
    else:
        labels = list(binding.get("typed") or [])
    for label in labels:
        safe = str(label)
        if f'label="{safe}"' not in text and f"<th>{safe}</th>" not in text:
            return False
    for label in binding.get("readonly") or []:
        if str(label).lower() not in text.lower():
            return False
    action = str(binding.get("action") or "")
    if action and action not in text:
        return False
    return True


def _kind(page_id: str, description: str) -> str:
    ident = re.sub(r"[^a-z0-9]+", "", page_id.lower())
    blob = f"{page_id} {description}".lower()
    if "signin" in ident or "login" in ident or "sign-in" in blob or "sign in" in blob:
        return "auth"
    if ident.endswith("list") or any(
        token in blob for token in ("each row", "columns", "dashboard")
    ):
        return "list"
    return "form"


def _record_for_page(
    page: dict[str, str], entities: list[dict[str, Any]], kind: str
) -> dict[str, Any] | None:
    from phase2.coverage import _fold

    page_key = _fold(page.get("id") or "")
    domain: list[dict[str, Any]] = []
    for row in entities:
        name = str(row.get("name") or "")
        key = _fold(name)
        if not key or key in _NOT_DOMAIN or key in _TECH:
            continue
        domain.append(row)
        if page_key and (key in page_key or page_key in key):
            return row
    if kind == "auth":
        return None
    if len(domain) == 1:
        return domain[0]
    return None


def _stated_labels(description: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    # Parenthetical notes ("set when saved, not typed") are not extra fields.
    flat = re.sub(r"\([^)]*\)", " ", description or "")
    for pattern in (r"Fields:\s*([^.]*)", r"Columns match the form:\s*([^.]*)"):
        match = re.search(pattern, flat, re.I)
        if not match:
            continue
        chunk = match.group(1)
        pieces = [chunk] if "," not in chunk else chunk.split(",")
        for piece in pieces:
            cleaned = re.sub(r"\([^)]*\)", "", piece).strip(" .")
            bits = (
                [bit.strip() for bit in cleaned.split(" and ")]
                if " and " in cleaned and "," not in chunk
                else [cleaned]
            )
            for bit in bits:
                key = bit.lower()
                if bit and key not in seen:
                    seen.add(key)
                    found.append(bit[:1].upper() + bit[1:] if bit[:1].islower() else bit)
    return found


def _is_readonly(description: str, label: str) -> bool:
    match = re.search(
        re.escape(label) + r"\s*(\([^)]*\))?",
        description or "",
        re.I,
    )
    note = match.group(1) if match else ""
    return bool(
        re.search(r"not typed|cannot be typed|set when|set to the current", note or "", re.I)
    )


def coverage_rows(brd_text: str, architecture_text: str) -> list[dict[str, Any]]:
    """Each BRD acceptance criterion, and whether this architecture covers it.

    A criterion is covered when the screen it names is in the architecture and
    every data-model field that screen describes is in the architecture too.
    """
    from phase2.architect import _entities
    from phase2.coverage import pages_from_brd

    entities = _entities(brd_text or "")
    pages = pages_from_brd(brd_text or "")
    modules = _markdown_section(architecture_text, "Modules")
    model = _markdown_section(architecture_text, "Data model")
    rows: list[dict[str, Any]] = []
    for item in _brd_requirements(brd_text or ""):
        blob = f"{item['title']}\n{item['body']}".lower()
        gaps: list[str] = []
        covered: list[str] = []
        checked = False
        for page in pages:
            if not _requirement_names_page(blob, page):
                continue
            checked = True
            screen_id = str(page.get("id") or "")
            if screen_id.lower() not in modules:
                gaps.append("screen " + screen_id)
            elif screen_id and screen_id not in covered:
                covered.append(screen_id)
            description = str(page.get("description") or "").lower()
            for name, fields in entities:
                for field in fields:
                    if field in {"id", "status"}:
                        continue
                    label = field.replace("_", " ")
                    if label not in description and field not in description:
                        continue
                    token = f"{name}.{field}"
                    if field not in model and label not in model:
                        gaps.append(token)
                    elif token not in covered:
                        covered.append(token)
        if not checked:
            gaps.append("not tied to a BRD screen")
        rows.append(
            {
                "id": item["id"],
                "title": item["title"],
                "criteria": item["criteria"],
                "satisfies": not gaps,
                "gaps": gaps,
                "covered": covered,
            }
        )
    return rows


_REQ_HEAD = re.compile(r"^###\s+(REQ-\d+-R\d+)\s+[—–-]\s+(.+)$", re.M)
_STOP = {
    "that", "this", "with", "from", "they", "their", "when", "then", "given",
    "have", "been", "will", "must", "into", "over", "each", "only", "than",
    "them", "were", "your", "about", "after", "before", "which", "there",
    "these", "those", "record", "current", "system", "named", "user", "signed",
}


def _markdown_section(text: str, heading: str) -> str:
    match = re.search(
        rf"^##\s+{re.escape(heading)}\b(.*?)(?=^## |\Z)",
        text or "",
        re.S | re.I | re.M,
    )
    if match:
        return match.group(1).lower()
    return (text or "").lower()


def _brd_requirements(brd_text: str) -> list[dict[str, str]]:
    matches = list(_REQ_HEAD.finditer(brd_text or ""))
    rows: list[dict[str, str]] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(brd_text)
        body = brd_text[start:end].strip()
        criteria = ""
        found = re.search(r"Acceptance criteria:\s*(.*)$", body, re.I | re.S)
        if found:
            # "**Acceptance criteria:**" leaves the closing stars on the body.
            criteria = re.sub(r"^\*+\s*", "", found.group(1).strip())
        rows.append(
            {
                "id": match.group(1),
                "title": match.group(2).strip(),
                "body": body,
                "criteria": criteria[:1200],
            }
        )
    return rows


def _words(text: str) -> set[str]:
    return {
        word
        for word in re.findall(r"[a-z0-9]+", text.lower())
        if len(word) > 3 and word not in _STOP
    }


def _overlap(left: set[str], right: set[str]) -> int:
    count = 0
    for word in left:
        for other in right:
            short, long = (word, other) if len(word) <= len(other) else (other, word)
            if len(short) > 3 and (short == long or long.startswith(short)):
                count += 1
                break
    return count


def _requirement_names_page(blob: str, page: dict[str, str]) -> bool:
    if _overlap(_words(blob), _words(f"{page.get('id') or ''} {page.get('description') or ''}")) >= 3:
        return True
    ident = re.sub(r"[^a-z0-9]", "", str(page.get("id") or "").lower())
    folded = re.sub(r"[^a-z0-9]", "", blob.lower())
    return bool(ident and ident in folded)


def _label_from_field(field: str) -> str:
    words = str(field or "").replace("_", " ").strip()
    return " ".join(word[:1].upper() + word[1:] for word in words.split())
