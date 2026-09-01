"""Resolving which GitHub accounts should review a gate.

Two mechanisms, because only one of them works everywhere:

* **Teams** (``@org/tier-vp``) are the mechanism the SoW commits to — they
  drive CODEOWNERS and branch protection, so enforcement lives in the
  repository rather than in application code. Teams require a GitHub
  *organisation*.
* **Individual users** are the fallback. On a personal account no team can
  exist, so requesting one silently reviews nothing. Naming the humans
  directly still puts the right people on the pull request; it just cannot be
  *enforced* by branch protection.

Falling back is deliberate. A gate nobody is asked to review is worse than one
enforced only by convention, because the first looks fine and quietly waits
forever.
"""

from __future__ import annotations

import logging

from . import db

log = logging.getLogger(__name__)

# Which tiers review which stage. Mirrors publish.STAGES, expressed as tiers
# so it can resolve to either teams or individuals.
STAGE_TIERS: dict[str, tuple[str, ...]] = {
    "requirement": ("md", "ceo"),
    "brd": ("md", "ceo", "vp"),
    "ui": ("vp",),
    "workitems": ("vp",),
}


def team_slugs(stage: str) -> list[str]:
    """GitHub team slugs for a stage, e.g. ``tier-vp``."""
    return [f"tier-{tier}" for tier in STAGE_TIERS.get(stage, ())]


def team_slug_for_department(department: str) -> str:
    return "tl-" + department.lower().replace(" & ", "-").replace(" ", "-")


def github_logins_for(stage: str, *, exclude_login: str | None = None) -> list[str]:
    """GitHub usernames of the internal users who may approve this stage.

    ``exclude_login`` drops the author: GitHub rejects a pull request that
    requests a review from its own creator, which would fail the whole call
    and leave nobody assigned.
    """
    tiers = STAGE_TIERS.get(stage, ())
    if not tiers:
        return []

    found = db.users().find(
        {"tierId": {"$in": list(tiers)}, "isClient": False, "githubLogin": {"$nin": [None, ""]}},
        {"githubLogin": 1},
    )
    logins = {u["githubLogin"] for u in found if u.get("githubLogin")}
    logins.discard(exclude_login)
    return sorted(logins)


def unmapped_reviewers(stage: str) -> list[str]:
    """Emails of users who could approve this stage but have no GitHub account mapped.

    Surfaced rather than ignored: an unmapped approver cannot be requested as a
    reviewer, and — more importantly — their approval cannot be matched back to
    an internal tier when the webhook arrives, so the gate would never advance.
    """
    tiers = STAGE_TIERS.get(stage, ())
    if not tiers:
        return []
    found = db.users().find(
        {"tierId": {"$in": list(tiers)}, "isClient": False,
         "$or": [{"githubLogin": {"$exists": False}}, {"githubLogin": None}, {"githubLogin": ""}]},
        {"email": 1},
    )
    return sorted(u.get("email", "") for u in found)
