"""Pre-approved stack profiles the Architect locks for a run.

An unconstrained agent recommends a different database on every project.
Locking one profile is the factory's answer. Context is not enforcement:
Gate 3 records the lock, later CI and SBOM checks prove it held.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class StackProfile:
    id: str
    frontend: str
    api: str
    data_access: str
    migrations: str
    tests: str
    lint: str
    database: str

    def dump(self) -> dict[str, str]:
        return asdict(self)

    def allowlist(self) -> frozenset[str]:
        return frozenset(self.dump().values())


NODE = StackProfile(
    id="node",
    frontend="React",
    api="Express",
    data_access="Prisma ORM",
    migrations="Prisma Migrate",
    tests="Vitest",
    lint="ESLint + Prettier",
    database="PostgreSQL",
)
PYTHON = StackProfile(
    id="python",
    frontend="React",
    api="FastAPI",
    data_access="SQLAlchemy",
    migrations="Alembic",
    tests="Pytest",
    lint="Ruff",
    database="PostgreSQL",
)
PROFILES = {NODE.id: NODE, PYTHON.id: PYTHON}

FORBIDDEN_RUNTIMES = ("jvm", "java", ".net", "golang", "go service")


def get(profile_id: str) -> StackProfile:
    if profile_id not in PROFILES:
        raise KeyError(f"unknown stack profile {profile_id}")
    return PROFILES[profile_id]


def choose(brd_text: str) -> StackProfile:
    """Deterministic lock. Analytics/batch language in the BRD picks Python."""
    lowered = brd_text.lower()
    if any(token in lowered for token in ("analytics", "batch job", "etl pipeline")):
        return PYTHON
    return NODE


def off_profile_hits(text: str, profile: StackProfile) -> list[str]:
    hits = []
    blob = text.lower()
    for token in FORBIDDEN_RUNTIMES:
        if token in blob:
            hits.append(token)
    allowed = {value.lower() for value in profile.allowlist()}
    rivals = {
        NODE.api.lower(),
        PYTHON.api.lower(),
        NODE.data_access.lower(),
        PYTHON.data_access.lower(),
        NODE.migrations.lower(),
        PYTHON.migrations.lower(),
        NODE.tests.lower(),
        PYTHON.tests.lower(),
        NODE.lint.lower(),
        PYTHON.lint.lower(),
    } - allowed
    for rival in sorted(rivals):
        if rival and rival in blob:
            hits.append(rival)
    return hits


def as_public(profile: StackProfile | dict[str, Any]) -> dict[str, str]:
    if isinstance(profile, StackProfile):
        return profile.dump()
    return {str(k): str(v) for k, v in dict(profile).items()}
