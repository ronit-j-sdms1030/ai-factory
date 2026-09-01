"""Administrative endpoints for wiring internal users to GitHub accounts.

The webhook resolves a pull-request reviewer to an internal tier by matching
``githubLogin`` on the user record. Without that mapping an approval arrives,
verifies, and is then discarded — the gate never advances and nothing obvious
explains why. These endpoints make the mapping visible and settable rather
than something to be hand-edited in the database.

Restricted to MD and CEO: the mapping decides whose GitHub approval can move a
requirement through a governance gate, so it is an authority change, not a
profile setting.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from .. import db, reviewers
from ..auth import current_user

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _require_leadership(user: dict = Depends(current_user)) -> dict:
    if user.get("isClient") or user.get("tierId") not in ("md", "ceo"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only MD or CEO can change GitHub account mappings",
        )
    return user


class MappingBody(BaseModel):
    email: str
    github_login: str


@router.get("/github-mappings")
def list_mappings(_: dict = Depends(_require_leadership)):
    """Every internal user and their GitHub account, plus what that breaks.

    ``blocked_stages`` names the gates that currently cannot advance, because
    no approver for them has a GitHub account mapped.
    """
    users = db.users().find(
        {"isClient": False},
        {"name": 1, "email": 1, "tierId": 1, "department": 1, "githubLogin": 1},
    )
    mapped = [
        {
            "email": u.get("email"),
            "name": u.get("name"),
            "tierId": u.get("tierId"),
            "department": u.get("department"),
            "githubLogin": u.get("githubLogin") or None,
        }
        for u in users
    ]
    blocked = [
        stage for stage in reviewers.STAGE_TIERS
        if not reviewers.github_logins_for(stage)
    ]
    return {
        "users": sorted(mapped, key=lambda u: (u["tierId"] or "", u["email"] or "")),
        "unmapped": [u["email"] for u in mapped if not u["githubLogin"]],
        "blocked_stages": blocked,
    }


@router.put("/github-mappings")
def set_mapping(body: MappingBody, _: dict = Depends(_require_leadership)):
    """Map one internal user to a GitHub login."""
    login = body.github_login.strip().lstrip("@")
    if not login:
        raise HTTPException(status_code=400, detail="github_login is required")

    # One GitHub account must not approve as two different people: the webhook
    # resolves login -> user -> tier, and a duplicate would make that lookup
    # ambiguous and the resulting authority arbitrary.
    clash = db.users().find_one({"githubLogin": login, "email": {"$ne": body.email}})
    if clash:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"GitHub account '{login}' is already mapped to {clash.get('email')}",
        )

    result = db.users().update_one(
        {"email": body.email.lower().strip(), "isClient": False},
        {"$set": {"githubLogin": login}},
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail=f"No internal user with email {body.email}")

    return {"ok": True, "email": body.email, "githubLogin": login}


@router.delete("/github-mappings/{email}")
def clear_mapping(email: str, _: dict = Depends(_require_leadership)):
    result = db.users().update_one(
        {"email": email.lower().strip(), "isClient": False}, {"$unset": {"githubLogin": ""}}
    )
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail=f"No internal user with email {email}")
    return {"ok": True, "email": email}
