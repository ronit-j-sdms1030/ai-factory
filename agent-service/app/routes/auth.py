"""Authentication endpoints.

Paths and response shapes match ``backend/src/routes/auth.routes.js`` exactly,
so the existing frontend works against either service without change.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr

from .. import db
from ..auth import current_user, hash_password, public_user, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginBody(BaseModel):
    email: str
    password: str


class RegisterBody(BaseModel):
    name: str
    email: EmailStr
    password: str


def _find(email: str, *, is_client: bool):
    return db.users().find_one({"email": (email or "").lower().strip(), "isClient": is_client})


def _sign_in(request: Request, user: dict) -> dict:
    payload = public_user(user)
    request.session["user"] = payload
    return {"user": payload}


@router.post("/login")
def login(body: LoginBody, request: Request):
    """Internal sign-in. Accounts are provisioned by seed, not self-registered."""
    user = _find(body.email, is_client=False)
    if not user or not verify_password(body.password, user.get("passwordHash", "")):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    return _sign_in(request, user)


@router.post("/client/register", status_code=status.HTTP_201_CREATED)
def client_register(body: RegisterBody, request: Request):
    email = body.email.lower().strip()
    if db.users().find_one({"email": email}):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="An account with this email already exists")

    doc = {
        "name": body.name,
        "email": email,
        "passwordHash": hash_password(body.password),
        "isClient": True,
        "tierId": None,
        "department": None,
    }
    doc["_id"] = db.users().insert_one(doc).inserted_id
    return _sign_in(request, doc)


@router.post("/client/login")
def client_login(body: LoginBody, request: Request):
    user = _find(body.email, is_client=True)
    if not user or not verify_password(body.password, user.get("passwordHash", "")):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    return _sign_in(request, user)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@router.get("/me")
def me(user: dict = Depends(current_user)):
    return {"user": user}


@router.get("/colleagues")
def colleagues(user: dict = Depends(current_user)):
    """Internal staff directory, used to pick discussion recipients."""
    if user.get("isClient"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Clients cannot list colleagues")
    found = db.users().find({"isClient": False, "_id": {"$ne": user["id"]}})
    return {
        "colleagues": [
            {
                "id": str(u["_id"]),
                "name": u.get("name"),
                "tierId": u.get("tierId"),
                "department": u.get("department") or None,
            }
            for u in found
            if str(u["_id"]) != user["id"]
        ]
    }
