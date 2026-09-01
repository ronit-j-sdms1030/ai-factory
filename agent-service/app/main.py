"""FastAPI application.

Mounts the same URL paths the Express backend serves, so the existing
frontend can be pointed at either during migration.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.sessions import SessionMiddleware

load_dotenv()

from .auth import SESSION_COOKIE, session_secret  # noqa: E402
from .routes import artifacts as artifact_routes  # noqa: E402
from .routes import auth as auth_routes  # noqa: E402
from .routes import webhooks as webhook_routes  # noqa: E402

app = FastAPI(title="AI Factory — Agent Service")

app.add_middleware(SessionMiddleware, secret_key=session_secret(), session_cookie=SESSION_COOKIE)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.environ.get("FRONTEND_ORIGIN", "http://localhost:5173")],
    allow_credentials=True,   # session cookie must travel cross-origin
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_routes.router)
app.include_router(artifact_routes.router)
app.include_router(webhook_routes.router)


@app.get("/api/health")
def health():
    return {"ok": True}
