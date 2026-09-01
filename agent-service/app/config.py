"""Hierarchy and approval rules.

Direct port of ``backend/src/config/hierarchy.config.js``. These values are
load-bearing for governance, so they are kept identical to the JavaScript
implementation rather than "improved" during the port — a divergence here
would silently change who is allowed to approve what.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal

TierId = Literal["md", "ceo", "vp", "pm", "tl"]

TIERS: dict[str, str] = {
    "md": "Managing Director",
    "ceo": "Chief Executive Officer",
    "vp": "Vice President",
    "pm": "Project Manager",
    "tl": "Team Lead",
}

# Fixed department taxonomy — every decomposition must use exactly these
# names so a team lead always knows which queue to check.
TEAM_DEPARTMENTS: list[str] = ["QA", "AI", "Development", "DevOps", "Sales & Marketing"]

# The department that owns the product's UI, and therefore the core
# application schema. Used as the default owner when decomposition leaves an
# entity unclaimed (see normalize_entity_ownership).
FRONTEND_OWNING_DEPARTMENT = "Development"


@dataclass
class ApprovalStep:
    approver_tiers: list[str]
    mode: str = "any"
    approved_by: list[dict] = field(default_factory=list)


# Note the asymmetry: VP is gate 1 for md/ceo/tl/client-originated work, but
# appears at gate 0 only for pm. A VP never approves their own requirement —
# vp-originated work escalates to md/ceo and has no VP gate at all.
APPROVAL_RULES: dict[str, list[list[str]]] = {
    "md": [["md"], ["vp"]],
    "ceo": [["ceo"], ["vp"]],
    "vp": [["md", "ceo"]],
    "pm": [["md", "ceo", "vp"]],
    "tl": [["md", "ceo"], ["vp"]],
    "client": [["md", "ceo"], ["vp"]],
}


def resolve_approval_chain(originator_tier_id: str | None) -> list[ApprovalStep]:
    """Build a fresh approval chain for an originator tier.

    A ``None`` tier means an external client, matching the JavaScript
    behaviour where a client account carries no internal tier.
    """
    key = "client" if originator_tier_id is None else originator_tier_id
    chain = APPROVAL_RULES.get(key)
    if chain is None:
        raise ValueError(f'No approval chain defined for originator tier "{originator_tier_id}"')
    return [ApprovalStep(approver_tiers=list(tiers)) for tiers in chain]


def originator_label(tier_id: str | None) -> str:
    return TIERS.get(tier_id or "", "Client")


# ── Intake budget ────────────────────────────────────────────────────────────
# Port of intakeBudget.js. The ceiling is enforced in code rather than by
# prompt: every turn re-sends the whole transcript, so cost grows with the
# SQUARE of conversation length. A real 41-turn run billed ~95k input tokens
# against ~12.7k for the same intake held to ten.
MIN_CLARIFYING_QUESTIONS = 4
MAX_CLARIFYING_QUESTIONS = 10


# ── Models ───────────────────────────────────────────────────────────────────
CHAT_MODEL = "anthropic/claude-haiku-4.5"
REPORT_MODEL = "openai/gpt-4o-mini"
DETAILED_REPORT_MODEL = "deepseek/deepseek-v3.2:nitro"

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def openrouter_api_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY is not set")
    return key


def mongodb_uri() -> str:
    return os.environ.get("MONGODB_URI", "mongodb://127.0.0.1:27017/ai_factory")
