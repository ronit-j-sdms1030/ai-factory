"""What the agents have been taught by being corrected.

Every reviewer edit is a statement about what the agent got wrong, and until
now each one was applied to a single screen and thrown away. The same
correction then arrived on the next requirement, and the one after that.

The distinction that makes this work is between a **rule** and an **edit**.
Changing a heading to "Q3 Attendance" is an edit — true of one screen and
nothing else. Changing "Jun 15, 02:15 PM" to "15/06/2025 14:15" looks like the
same kind of change and is not: it is a house date format, and an agent told
about it will get every future date right. Formatting, wording conventions,
currency, capitalisation and density preferences generalise. Content does not.

Formatting rules activate on their own. The worst case is a screen using a
date format somebody corrects again, which is self-limiting, and requiring
approval for it would mean nobody ever benefits from the loop. Rules that
change behaviour or structure wait for a human, because a bad one there
degrades every screen generated afterwards and the audit trail would show the
agent as having always behaved that way.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from . import db

log = logging.getLogger(__name__)

# Applied without asking. These change how something is written, not what the
# screen does, so a wrong one is visible immediately and corrected the same way
# it was learned.
AUTO_KINDS = {"formatting", "wording", "convention"}

# The prompt cannot grow without bound, and a rule nobody has needed in fifty
# screens is noise competing with the ones that matter.
MAX_ACTIVE = 25


def _collection():
    return db.db()["lessons"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def record(agent: str, rule: str, kind: str, evidence: str, source: str) -> dict[str, Any]:
    """Store a proposed rule, or count a repeat of one already known.

    Repeats matter more than firsts: a correction made once may be taste, and
    the same correction three times is a house standard. ``timesSeen`` is what
    the review screen sorts by.
    """
    normalised = " ".join(rule.lower().split())
    existing = _collection().find_one({"agent": agent, "normalised": normalised})
    if existing:
        _collection().update_one(
            {"_id": existing["_id"]},
            {"$inc": {"timesSeen": 1}, "$set": {"lastSeenAt": _now()}},
        )
        return {**existing, "timesSeen": existing.get("timesSeen", 1) + 1}

    doc = {
        "agent": agent,
        "rule": rule.strip(),
        "normalised": normalised,
        "kind": kind,
        "evidence": evidence,
        "source": source,
        "status": "active" if kind in AUTO_KINDS else "proposed",
        "timesSeen": 1,
        "createdAt": _now(),
        "lastSeenAt": _now(),
    }
    _collection().insert_one(doc)
    log.info("learned a %s rule for %s: %s", kind, agent, rule[:80])
    return doc


def active(agent: str) -> list[str]:
    """The rules an agent should currently be following."""
    found = (
        _collection()
        .find({"agent": agent, "status": "active"})
        .sort([("timesSeen", -1), ("createdAt", 1)])
        .limit(MAX_ACTIVE)
    )
    return [doc["rule"] for doc in found]


def prompt_section(agent: str) -> str:
    """Active rules as a prompt fragment, or empty when there are none."""
    rules = active(agent)
    if not rules:
        return ""
    return (
        "LEARNED FROM PREVIOUS REVIEWS — these are corrections reviewers have already "
        "made to this pipeline's output. Follow them from the start rather than being "
        "corrected again:\n" + "\n".join(f"- {rule}" for rule in rules)
    )


def listing(agent: str | None = None) -> list[dict[str, Any]]:
    query = {"agent": agent} if agent else {}
    found = _collection().find(query).sort([("status", 1), ("timesSeen", -1)])
    return [
        {
            "id": str(doc["_id"]),
            "agent": doc["agent"],
            "rule": doc["rule"],
            "kind": doc["kind"],
            "evidence": doc.get("evidence", ""),
            "source": doc.get("source", ""),
            "status": doc["status"],
            "timesSeen": doc.get("timesSeen", 1),
            "createdAt": doc.get("createdAt"),
        }
        for doc in found
    ]


def set_status(lesson_id: str, status: str) -> bool:
    """Activate or dismiss a rule. Dismissed rules are kept, not deleted.

    A rule somebody rejected is itself a fact about this deployment, and
    deleting it invites the extractor to propose the same thing next week.
    """
    from bson import ObjectId
    from bson.errors import InvalidId

    if status not in ("active", "proposed", "dismissed"):
        raise ValueError(f"unknown status '{status}'")
    try:
        oid = ObjectId(lesson_id)
    except (InvalidId, TypeError):
        return False
    return _collection().update_one({"_id": oid}, {"$set": {"status": status}}).modified_count > 0
