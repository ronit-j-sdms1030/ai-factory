"""Pre-approved stack profiles the Architect locks for a run.

An unconstrained agent recommends a different database on every project.
Locking one profile is the factory's answer. Context is not enforcement:
Gate 3 records the lock, later CI and SBOM checks prove it held.
"""

from __future__ import annotations

import re
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

# One product: a React screen and a Node.js or Python API. Everything else in
# the capability boundary is the same kind of miss, not a special case per product.
_NEGATION = re.compile(r"\b(?:no|not|without|never|don'?t|do not)\b", re.I)
_WEB_APP = re.compile(
    r"\b(?:web\s*apps?|browser|react|dashboards?|portals?|screens?|forms?|"
    r"catalog(?:ue)?s?|loans?|records?|login|admin|websites?)\b",
    re.I,
)
STACK_LIMIT = (
    "This factory only ships a React browser app with a Node.js or Python API."
)
BOUNDARY_MARK = "outside this factory's stack"


@dataclass(frozen=True)
class Boundary:
    id: str
    label: str
    patterns: tuple[re.Pattern[str], ...]


def _p(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.I)


# Strong product signals only. "chat", "on their phones", "real-time", and
# "mobile browser" are wording, not a different product — intake asks those.
BOUNDARIES: tuple[Boundary, ...] = (
    Boundary(
        "conversation_interface",
        "A call bot or chatbot",
        (
            _p(
                r"\b(?:chat\s*bots?|call\s*bots?|voice\s*bots?|phone\s*bots?|"
                r"whatsapp\s+bots?|telegram\s+bots?|slack\s+bots?|discord\s+bots?|"
                r"ivr|interactive voice(?: response)?|dialogflow|voiceflow|"
                r"conversational\s+(?:agent|bot)|virtual\s+agent)\b"
            ),
        ),
    ),
    Boundary(
        "native_mobile",
        "A native mobile app",
        (
            _p(
                r"\b(?:ios|iphone|ipad|android)\s+apps?\b|\bapp\s*store\b|\bplay\s*store\b|"
                r"\bnative\s+mobile\b|\breact\s*native\b|\bflutter\b"
            ),
        ),
    ),
    Boundary(
        "desktop",
        "A desktop application",
        (_p(r"\b(?:desktop\s+(?:apps?|applications?)|electron\s+apps?|installers?)\b"),),
    ),
    Boundary(
        "embedded",
        "Device firmware",
        (
            _p(
                r"\b(?:firmwares?|embedded\s+software|microcontrollers?|"
                r"device\s+drivers?|arduino)\b"
            ),
        ),
    ),
    Boundary(
        "game",
        "A video game",
        (
            _p(
                r"\b(?:video\s+games?|computer\s+games?|mobile\s+games?|"
                r"game\s+engines?|unity\s+(?:engine|game)|unreal(?:\s+engine)?|"
                r"real-time\s+rendering)\b"
            ),
        ),
    ),
    Boundary(
        "other_runtime",
        "A runtime other than Node.js or Python",
        (
            _p(r"\b(?:jvm|golang|spring\s+boot)\b|\.net\b"),
            _p(r"\bjava\b(?!script)"),
            _p(r"\bgo\s+services?\b"),
        ),
    ),
    Boundary(
        "training_platform",
        "An ML training or data platform",
        (
            _p(
                r"\b(?:model\s+training|ml\s+platform|machine\s+learning\s+platform|"
                r"train(?:ing)?\s+(?:and\s+serve\s+)?models?|feature\s+store|"
                r"data\s+engineering\s+platform)\b"
            ),
        ),
    ),
    Boundary(
        "safety_critical",
        "Safety-critical software",
        (
            _p(
                r"\b(?:safety-critical|medical\s+devices?|avionics|"
                r"iso\s*26262|do-178|iec\s*62304)\b"
            ),
        ),
    ),
    Boundary(
        "latency_platform",
        "A high-frequency or telemetry platform",
        (
            _p(
                r"\b(?:high-frequency\s+trading|hft|sub-millisecond|"
                r"telemetry\s+ingestion)\b"
            ),
        ),
    ),
)


def _matched(text: str) -> list[Boundary]:
    """Boundaries the requester asked for, ignoring a nearby no/not/without."""
    found: list[Boundary] = []
    blob = text or ""
    for boundary in BOUNDARIES:
        hit = False
        for pattern in boundary.patterns:
            for match in pattern.finditer(blob):
                window = blob[max(0, match.start() - 48) : match.start()]
                if _NEGATION.search(window):
                    continue
                hit = True
                break
            if hit:
                break
        if hit:
            found.append(boundary)
    return found


def _join_labels(labels: list[str]) -> str:
    if not labels:
        return "That request"
    if len(labels) == 1:
        return labels[0]
    return ", ".join(labels[:-1]) + " and " + labels[-1]


def delivery_fit(text: str) -> dict[str, Any]:
    """Tell a browser app apart from a product this stack cannot ship.

    A library with loan screens stays in. "No chatbot, just the loan screens"
    stays in. A WhatsApp bot, an iOS app, firmware, or a Java service does not,
    until a browser app is also described — then the browser app continues and
    the other product is recorded as out of scope.
    """
    hits = _matched(text or "")
    web = _WEB_APP.search(text or "") is not None
    return {
        "web_app": web,
        "outside": [hit.id for hit in hits],
        "labels": [hit.label for hit in hits],
        "core_outside": bool(hits) and not web,
    }


def exclusion_line(text: str) -> str:
    labels = _join_labels(delivery_fit(text)["labels"])
    return f"{labels}. {STACK_LIMIT}"


def boundary_question(text: str) -> str:
    labels = _join_labels(delivery_fit(text)["labels"])
    return (
        "This factory only ships a browser app: React on the front, Node.js or Python "
        f"on the API. {labels} is {BOUNDARY_MARK}, so it cannot be generated. "
        "Is that the whole request, or is there a website or staff screen underneath it?"
    )


def outside_only_scope(text: str) -> dict[str, Any]:
    """Scope report when nothing in the request is a browser application."""
    title = " ".join((text or "").split())
    if len(title) > 80:
        title = title[:77].rsplit(" ", 1)[0]
    labels = _join_labels(delivery_fit(text)["labels"])
    return {
        "type": "scope_report",
        "title": title or labels,
        "users": "Not specified — no browser application was described",
        "current_state": f"The request is for {labels[0].lower() + labels[1:]}.",
        "in_scope": [
            "No React + Node or Python application — a browser product was not described"
        ],
        "out_of_scope": [exclusion_line(text)],
        "success": f"No product is generated for {labels[0].lower() + labels[1:]}.",
        "non_functional": [
            "Stack is React in the browser, with one Node.js or Python API"
        ],
        "assumptions": [],
        "open_questions": [
            "Describe a browser application if one should be built instead."
        ],
    }


def merge_exclusions(out_of_scope: list[str], text: str) -> list[str]:
    """Keep a browser app's scope, and name every product the stack cannot ship."""
    if not delivery_fit(text)["outside"]:
        return list(out_of_scope)
    line = exclusion_line(text)
    existing = " ".join(out_of_scope).lower()
    if "only ships a react browser app" in existing:
        return list(out_of_scope)
    return [line, *out_of_scope]


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
