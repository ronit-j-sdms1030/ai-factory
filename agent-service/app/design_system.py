"""The design-system skill file — what constrains every generated screen.

SoW 11.0 commits that the UI is generated "as working React screens
constrained by a Jakson design-system skill file (tokens, components,
accessibility rules)". Until this existed the only constraint was a paragraph
of prose hardcoded in the UI agent, which is not a skill file in any useful
sense: the client could not read it, could not change it, and could not tell
which version produced a screen they were approving.

**Stored, not hardcoded.** SoW 4.0 requires skill files to be
version-controlled with change history and approver identity, so every edit
appends a revision naming who made it. The current text is read per
generation rather than cached, so a correction takes effect on the next run
instead of the next deploy.

**It decides the styling mechanism, and the preview follows it.** This is the
part that was silently broken: the models emitted Tailwind utility classes on
their own initiative, while the preview page loaded no CSS at all, so ten of
fourteen screens on a real requirement rendered as unstyled HTML and nobody
noticed. Naming the mechanism in one place — read by both the agent writing
the screen and the page rendering it — is what stops the two disagreeing.

The default below is deliberately a *starting* design system rather than an
empty file. An empty skill file would return the pipeline to models guessing,
which is the failure this replaces.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from . import db

_KEY = "designSystem"

# How many revisions to keep. Enough to see how a convention evolved and to
# revert a bad edit, bounded so one document cannot grow without limit.
MAX_REVISIONS = 20

# The styling mechanism the whole pipeline agrees on. Changing this means
# changing DEFAULT_SKILL and the preview's stylesheet together — they are two
# halves of one decision, which is why the constant lives here rather than in
# either file that consumes it.
STYLING = "tailwind"

DEFAULT_SKILL = """# Design system — skill file

Every screen this pipeline generates must follow the rules below. They are
house standards, not suggestions: a screen that ignores them is a defect even
if it renders.

## Styling mechanism

Use **Tailwind CSS utility classes** via `className`. Tailwind is loaded in
the preview environment, so utility classes resolve. Do not write `<style>`
blocks, do not use inline `style={{...}}` objects for layout, and do not
invent class names of your own — an unrecognised class silently does nothing.

## Colour tokens

| Purpose | Class |
|---|---|
| Primary action, active state | `bg-slate-800` with `text-white` |
| Primary hover | `hover:bg-slate-700` |
| Page background | `bg-slate-50` |
| Card and panel surface | `bg-white` |
| Default border | `border-slate-200` |
| Primary text | `text-slate-900` |
| Secondary text | `text-slate-500` |
| Success / approved | `bg-emerald-50` with `text-emerald-700` |
| Warning / pending | `bg-amber-50` with `text-amber-700` |
| Danger / rejected | `bg-red-50` with `text-red-700` |

Two accent colours plus neutrals. Do not introduce a third accent.

## Spacing and layout

- Page padding `p-6`; gap between major blocks `gap-6`.
- Cards: `bg-white rounded-lg border border-slate-200 p-6`.
- Every screen has a persistent left sidebar or top nav, then a content area.
- Tables: header row `bg-slate-50 text-slate-500 text-sm font-medium`,
  body rows separated by `border-t border-slate-200`.

## Typography

- Page title `text-2xl font-semibold text-slate-900`.
- Section heading `text-lg font-medium text-slate-900`.
- Body `text-sm text-slate-700`. Never below `text-xs`.

## Components

- **Buttons** — `px-4 py-2 rounded-md text-sm font-medium`. One primary action
  per screen; everything else `bg-white border border-slate-200`.
- **Inputs** — `w-full px-3 py-2 rounded-md border border-slate-200 text-sm`,
  always with a `<label>`.
- **Status** — a pill, `px-2 py-1 rounded-full text-xs font-medium`, using the
  success/warning/danger tokens above.

## Accessibility

- Every input has a `<label>` with a matching `htmlFor` and `id`.
- Every icon-only control has an `aria-label`.
- Status is never conveyed by colour alone — always include the text.
- Interactive elements are real `<button>` or `<a>`, never a clickable `<div>`.
- Keep the visible focus ring; never set `outline-none` without a replacement.

## Content

Every number, name, date and status is a specific plausible value. Never
`---`, `N/A`, `TBD` or `Lorem ipsum`.
"""


def _collection():
    return db.db()["settings"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def current() -> dict[str, Any]:
    """The skill file in force, with the metadata needed to cite a version.

    Falls back to the built-in default rather than to nothing: a deployment
    that has never opened the settings page should still generate screens
    against a real design system.
    """
    doc = _collection().find_one({"_id": _KEY}) or {}
    content = (doc.get("content") or "").strip()
    if not content:
        return {
            "content": DEFAULT_SKILL,
            "version": 0,
            "isDefault": True,
            "updatedBy": None,
            "updatedAt": None,
            "styling": STYLING,
        }
    return {
        "content": content,
        "version": doc.get("version", 1),
        "isDefault": False,
        "updatedBy": doc.get("updatedBy"),
        "updatedAt": doc.get("updatedAt"),
        "styling": doc.get("styling") or STYLING,
    }


def set_content(content: str, actor_id: str) -> dict[str, Any]:
    """Replace the skill file, keeping who changed it and what it said before.

    An empty submission restores the built-in default rather than leaving the
    agent unconstrained — "no design system" is never a state worth being one
    edit away from.
    """
    text = (content or "").strip()
    existing = _collection().find_one({"_id": _KEY}) or {}

    revisions = list(existing.get("revisions") or [])
    if existing.get("content"):
        revisions.append(
            {
                "content": existing["content"],
                "version": existing.get("version", 1),
                "updatedBy": existing.get("updatedBy"),
                "updatedAt": existing.get("updatedAt"),
            }
        )
    revisions = revisions[-MAX_REVISIONS:]

    if not text:
        # Reverting to the built-in default is itself a change, and the text
        # being discarded is what someone auditing the revert needs to see.
        _collection().update_one(
            {"_id": _KEY},
            {"$set": {"content": "", "revisions": revisions, "updatedBy": actor_id, "updatedAt": _now()}},
            upsert=True,
        )
        return current()

    _collection().update_one(
        {"_id": _KEY},
        {
            "$set": {
                "content": text,
                "version": existing.get("version", 0) + 1,
                "updatedBy": actor_id,
                "updatedAt": _now(),
                "styling": STYLING,
                "revisions": revisions,
            }
        },
        upsert=True,
    )
    return current()


def history() -> list[dict[str, Any]]:
    """Previous versions, newest first — the change history SoW 4.0 requires."""
    doc = _collection().find_one({"_id": _KEY}) or {}
    return list(reversed(doc.get("revisions") or []))


def prompt_section() -> str:
    """The skill file as a prompt fragment for the UI agent."""
    skill = current()
    return (
        "DESIGN SYSTEM — this is the house skill file and it governs every screen "
        "you write. Follow it exactly; it outranks your own preferences.\n\n"
        f"{skill['content']}"
    )
