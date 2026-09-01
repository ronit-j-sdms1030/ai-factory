"""Authentication and session handling.

Password hashes are read and written in the same bcrypt format the Express
backend uses, so accounts provisioned by the existing seed script log in here
unchanged and both services can run against one user collection.

Sessions are signed cookies rather than server-side session storage. The
Express app uses ``express-session``; this is not wire-compatible with it, so
during migration a user authenticates separately against each service. Sharing
sessions would mean either a shared session store or JWTs, which is a decision
for the integration step rather than something to assume here.
"""

from __future__ import annotations

import json
import os
from typing import Any

import bcrypt
from fastapi import Depends, HTTPException, Request, status

SESSION_COOKIE = "ai_factory_session"


def session_secret() -> str:
    secret = os.environ.get("SESSION_SECRET")
    if not secret:
        # Refuse to fall back to a default: a predictable signing key means
        # forgeable sessions, and this guards approval authority.
        raise RuntimeError("SESSION_SECRET is not set")
    return secret


def hash_password(plain: str) -> str:
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt(rounds=10)).decode()


def verify_password(plain: str, hashed: str) -> bool:
    if not plain or not hashed:
        return False
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except ValueError:
        # Malformed stored hash — treat as a failed login rather than a 500.
        return False


def public_user(user: dict[str, Any]) -> dict[str, Any]:
    """The user shape the frontend expects. Never includes the password hash."""
    return {
        "id": str(user["_id"]),
        "name": user.get("name"),
        "email": user.get("email"),
        "tierId": user.get("tierId"),
        "isClient": bool(user.get("isClient")),
        "department": user.get("department") or None,
    }


def _forwarded_user(request: Request) -> dict[str, Any] | None:
    """Identity forwarded by the Express proxy, if it can be trusted.

    During migration the frontend still talks to Express, which authenticates
    the session and forwards the user here. Trusting that header is only safe
    if the caller proves it is Express, so it is accepted solely when a shared
    secret matches.

    Fails closed: with ``AGENT_SERVICE_TOKEN`` unset, forwarded identities are
    ignored entirely rather than trusted by default. An unset secret must not
    turn into an open door.
    """
    header = request.headers.get("x-actor")
    if not header:
        return None

    expected = os.environ.get("AGENT_SERVICE_TOKEN")
    if not expected or request.headers.get("x-agent-service-token") != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Forwarded identity rejected: missing or invalid agent service token",
        )

    try:
        user = json.loads(header)
    except json.JSONDecodeError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Malformed X-Actor header")

    if not isinstance(user, dict) or not user.get("id"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Malformed X-Actor header")
    return user


def current_user(request: Request) -> dict[str, Any]:
    """Dependency for routes that require a signed-in user.

    Accepts either a direct session on this service or an identity forwarded
    by the Express proxy.
    """
    user = _forwarded_user(request) or request.session.get("user")
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not signed in")
    return user


def require_internal(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    """Reject client accounts from internal-only routes."""
    if user.get("isClient"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Clients cannot perform this action")
    return user


def actor_from(user: dict[str, Any]) -> dict[str, Any]:
    """Translate a session user into the shape the state machine expects."""
    return {"user_id": user["id"], "tier_id": user.get("tierId")}
