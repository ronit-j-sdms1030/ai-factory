"""Versioned prompt fragments — the tuning decisions, held as artefacts.

SoW 4.0 commits that "prompt templates and model versions are stored as
versioned repository artefacts with change history and approver identity", and
SoW 7.0 lists ``prompt version`` among the audit fields every run must record.
Neither was true while every prompt was a string literal inside an agent
function: the text was in Git, but only as ordinary code, changed by whoever
edits Python and reviewed as a diff by another developer. Nobody accountable
for the *output* ever approved a change to the instructions that produce it.

**What is exposed, and what is not.** Only fragments that are genuine tuning
decisions live here — the judgement a reviewer might legitimately want to
adjust after seeing a few documents. The instructions that hold a schema
contract together stay in code: "return the complete component", "no imports",
"fill every field". Those are not preferences, and a settings page that can
break the parser is a worse failure than one that cannot express a preference.

**Defaults stay in code on purpose.** A deployment that has never opened the
settings page runs the same prompts as one that has, and a bad edit can be
reverted to a known-good text that lives in version control rather than in a
database row. The stored value is an override, not the source.

Mirrors ``design_system`` deliberately — same storage shape, same revision
history, same restriction to MD, CEO and VP — because they are the same kind
of thing: instructions that silently govern every artefact generated
afterwards.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from . import db

log = logging.getLogger(__name__)

_KEY_PREFIX = "prompt:"

# Enough to see how wording evolved and to revert a bad edit, bounded so one
# document cannot grow without limit.
MAX_REVISIONS = 20


_BRD_RIGOUR = """
DESIGN RIGOUR — work through each of these before committing to any technology:
- Fitness for THIS environment, not generic suitability. How does the component behave under this build's actual physical conditions, scale, duty cycle and real user behaviour? A part that is obvious in one setting is often wrong one setting over, and the difference is usually a property of the environment the requirement already described.
- Name the standard. If an established industry standard or published specification governs this problem domain, build on it or state explicitly why not. Reaching for a general-purpose or hobbyist-tier component where a mature domain standard exists is a design error, not a cost saving.
- Deliver what was promised. Check each capability the requirement promises is genuinely delivered, not a weaker cousin of it. If the design can only deliver a reduced version, say so in openQuestions rather than quietly narrowing scope.
- No fictional precision. Every field in dataModel must be something the chosen components can actually produce. Inventing a field nothing can populate makes the whole document untrustworthy.
- Failure and safety. State what happens when the system fails or loses power, and what the safe state is. Where the build touches physical systems, public spaces, money or regulated data, name the applicable safety or compliance constraint and how the design honours it.
""".strip()


_BRD_CRITIQUE = """
You are a senior engineer with deep domain experience, reviewing a proposed design before it reaches a build team. You did not write it. Find where it will fail in the real world — do not praise it and do not nitpick wording.

Judge fitness for purpose: (1) will each component work under this build's real operating conditions — physical environment, scale, duty cycle, user behaviour? (2) does an established standard already govern this domain that the design ignored in favour of a general-purpose substitute? (3) is every promised capability genuinely delivered, or has one been quietly downgraded? (4) does the data model claim fields the chosen components cannot produce? (5) where the build touches physical systems, public spaces, money or regulated data, is failure and safe-state behaviour defined?

Report only defects you can tie to a concrete failure circumstance. If the design is sound, return an empty findings list — a clean review is a valid outcome and filler findings are worse than none.
""".strip()


_UI_VISUAL_POLISH = (
    "Make this look like a real, professionally designed product rather than a wireframe: a proper "
    "layout, real spacing, and readable typography with clear hierarchy. Every number, name, date "
    "and status shown must be a specific plausible value — never a literal placeholder like '---', "
    "'N/A' or 'TBD'. Invent realistic mock content instead."
)


_UI_PRODUCT_SHAPE = (
    "Organise navigation around real user-facing workflows for this product. Never create a screen or "
    "nav item named after an internal department or team. Internal engineering concerns belong folded "
    "into a single clearly-internal area, not given equal billing beside real product features."
)


_DECOMPOSITION_CONTRACT = """
THESE PACKAGES MUST RECOMBINE INTO ONE WORKING PRODUCT. Each department generates its code from its own package alone, never seeing another's, so what you write here is the only thing keeping the modules compatible:
- Shared entities must be spelled identically in every package, character for character, copied verbatim from the BRD data model. 'Return_Items' in one package and 'ReturnItems' in another produces foreign keys that do not resolve — a broken build, not a cosmetic mismatch.
- Include entities a department only reads, marked owned_by_this_department false, or it will invent its own incompatible version of the same thing.
- Exactly one department owns each entity — never zero. An entity read-only in every package means nobody generates its schema and the table does not exist.

Do not copy the BRD's diagrams or full security design into every package.
""".strip()


# Each entry is a fragment a reviewer may legitimately tune. ``agent`` is the
# agent it governs, so the settings page can group them the way the model
# picker does.
PROMPTS: dict[str, dict[str, Any]] = {
    "brd.rigour": {
        "agent": "brd",
        "label": "BRD design rigour",
        "detail": "The checks the BRD agent works through before committing to any technology.",
        "default": _BRD_RIGOUR,
    },
    "brd.critique": {
        "agent": "brd",
        "label": "BRD critique brief",
        "detail": "What the fresh-context reviewer looks for. Tightening this changes what gets caught.",
        "default": _BRD_CRITIQUE,
    },
    "ui.visualPolish": {
        "agent": "ui",
        "label": "UI visual expectations",
        "detail": "The floor every screen must clear. House style belongs in the design system, not here.",
        "default": _UI_VISUAL_POLISH,
    },
    "ui.productShape": {
        "agent": "ui",
        "label": "UI navigation shape",
        "detail": "How screens are organised — by user workflow rather than by internal team.",
        "default": _UI_PRODUCT_SHAPE,
    },
    "decomposition.contract": {
        "agent": "decomposition",
        "label": "Department integration contract",
        "detail": "The rules that keep independently generated packages compatible.",
        "default": _DECOMPOSITION_CONTRACT,
    },
}


def _collection():
    return db.db()["settings"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _doc_id(name: str) -> str:
    return f"{_KEY_PREFIX}{name}"


def current(name: str) -> dict[str, Any]:
    """One fragment: the text in force, and where it came from."""
    if name not in PROMPTS:
        raise ValueError(f"unknown prompt '{name}'")
    meta = PROMPTS[name]
    doc = _collection().find_one({"_id": _doc_id(name)}) or {}
    stored = (doc.get("content") or "").strip()
    return {
        "name": name,
        "agent": meta["agent"],
        "label": meta["label"],
        "detail": meta["detail"],
        "content": stored or meta["default"],
        "version": doc.get("version", 0) if stored else 0,
        "isDefault": not stored,
        "updatedBy": doc.get("updatedBy") if stored else None,
        "updatedAt": doc.get("updatedAt") if stored else None,
    }


def text(name: str) -> str:
    """The fragment an agent should use right now.

    Best-effort: an unreachable database returns the built-in default rather
    than an empty string. Generating against the shipped prompt is a correct
    outcome; generating against no instructions is not.
    """
    try:
        return current(name)["content"]
    except Exception as exc:  # noqa: BLE001
        log.warning("could not read prompt '%s', using the default: %s", name, exc)
        return PROMPTS[name]["default"]


def set_text(name: str, content: str, actor_id: str) -> dict[str, Any]:
    """Replace a fragment, keeping who changed it and what it said before.

    An empty submission restores the built-in default — the shipped text is
    always one action away, so a bad edit never has to be reconstructed from
    memory.
    """
    if name not in PROMPTS:
        raise ValueError(f"unknown prompt '{name}'")

    body = (content or "").strip()
    existing = _collection().find_one({"_id": _doc_id(name)}) or {}

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

    if not body:
        # Reverting to the shipped default is itself a change, and the text
        # being discarded is exactly what someone auditing the revert needs to
        # see. Clearing without recording it lost the only copy.
        _collection().update_one(
            {"_id": _doc_id(name)},
            {"$set": {"content": "", "revisions": revisions, "updatedBy": actor_id, "updatedAt": _now()}},
            upsert=True,
        )
        return current(name)

    _collection().update_one(
        {"_id": _doc_id(name)},
        {
            "$set": {
                "content": body,
                "version": existing.get("version", 0) + 1,
                "updatedBy": actor_id,
                "updatedAt": _now(),
                "revisions": revisions,
            }
        },
        upsert=True,
    )
    return current(name)


def history(name: str) -> list[dict[str, Any]]:
    """Previous versions of one fragment, newest first."""
    doc = _collection().find_one({"_id": _doc_id(name)}) or {}
    return list(reversed(doc.get("revisions") or []))


def listing() -> list[dict[str, Any]]:
    """Every fragment, for the settings page."""
    return [current(name) for name in PROMPTS]


def versions(agent: str | None = None) -> dict[str, int]:
    """Fragment name to version in force — the audit stamp.

    Recorded on a generated artefact so "which instructions produced this?"
    has an answer that survives the prompts moving on. SoW 7.0 names prompt
    version as a required audit field.
    """
    return {
        name: current(name)["version"]
        for name, meta in PROMPTS.items()
        if agent is None or meta["agent"] == agent
    }


def as_markdown(agent: str) -> str:
    """One agent's fragments as a committable document.

    Written into the requirement's folder beside the artefact it produced, so
    a reviewer reading an approved BRD can see the instructions it was written
    against without resolving a version number against a database.
    """
    parts = []
    for name, meta in PROMPTS.items():
        if meta["agent"] != agent:
            continue
        entry = current(name)
        parts.append(
            f"## {entry['label']}\n\n"
            f"*`{name}` · version {entry['version']}"
            f"{' (built-in default)' if entry['isDefault'] else ''}*\n\n"
            f"{entry['content']}\n"
        )
    if not parts:
        return ""
    return f"# Prompts used — {agent} agent\n\n" + "\n".join(parts)
