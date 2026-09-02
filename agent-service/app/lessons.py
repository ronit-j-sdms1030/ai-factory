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

Formatting rules activate on their own, but only from a repeat. A correction
made once may be taste; the same correction on a second screen is a house
standard. Auto-activating on the first sighting meant one reviewer's opinion
on one screen silently became a standing instruction for every client
afterwards. Rules that change behaviour or structure wait for a human
regardless of repeats, because a bad one there degrades every screen
generated afterwards and the audit trail would show the agent as having
always behaved that way.

A rule a human dismisses stays dismissed even if the same correction is made
again later — the repeat does not override their decision, since resurrecting
a rejected rule by attrition would make the dismissal meaningless.
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


def record(
    agent: str, rule: str, kind: str, evidence: str, source: str, conflicts: str = ""
) -> dict[str, Any]:
    """Store a proposed rule, or promote a repeat of one already known.

    Every rule starts ``proposed``, including formatting ones. It is promoted
    to ``active`` only when the same normalised rule is seen a second time —
    ``timesSeen`` is what the review screen sorts by, and is also the gate on
    auto-activation. A ``dismissed`` rule is never promoted by a repeat: a
    human already decided against it, and letting attrition overrule that
    would make dismissal meaningless. A rule flagged as conflicting with an
    existing one is never auto-promoted either, no matter how many times it
    recurs — that decision needs a human to say which rule wins.
    """
    normalised = " ".join(rule.lower().split())
    existing = _collection().find_one({"agent": agent, "normalised": normalised})
    if existing:
        times_seen = existing.get("timesSeen", 1) + 1
        update: dict[str, Any] = {"timesSeen": times_seen, "lastSeenAt": _now()}
        if (
            existing["status"] == "proposed"
            and kind in AUTO_KINDS
            and times_seen >= 2
            and not existing.get("conflictsWith")
        ):
            update["status"] = "active"
        _collection().update_one({"_id": existing["_id"]}, {"$set": update})
        return {**existing, **update}

    doc = {
        "agent": agent,
        "rule": rule.strip(),
        "normalised": normalised,
        "kind": kind,
        "evidence": evidence,
        "source": source,
        "conflictsWith": conflicts or None,
        "status": "proposed",
        "timesSeen": 1,
        "createdAt": _now(),
        "lastSeenAt": _now(),
    }
    _collection().insert_one(doc)
    log.info("proposed a %s rule for %s: %s", kind, agent, rule[:80])
    return doc


def active(agent: str) -> list[str]:
    """The rules an agent should currently be following."""
    return [doc["rule"] for doc in _active_docs(agent)]


def active_ids(agent: str) -> list[str]:
    """Ids of the currently active rules, in the order the prompt renders them.

    Recorded on a generated artefact so it is later possible to say exactly
    which instructions produced it — the rule set moves on, but the artefact
    should not silently change what it is attributed to.
    """
    return [str(doc["_id"]) for doc in _active_docs(agent)]


def _active_docs(agent: str) -> list[dict[str, Any]]:
    return list(
        _collection()
        .find({"agent": agent, "status": "active"})
        .sort([("timesSeen", -1), ("createdAt", 1)])
        .limit(MAX_ACTIVE)
    )


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
            "conflictsWith": doc.get("conflictsWith"),
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
