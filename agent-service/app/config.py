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
# Every model is overridable from the environment, because which one suits a
# stage is a question answered by running it, not by reading a docs page — and
# a bad choice should be a one-line .env change rather than a deploy.
#
# A "groq/" prefix routes to Groq; anything else goes to OpenRouter.
def _model(name: str, default: str) -> str:
    return os.environ.get(name, default)


# Conversational. Plain natural language, so a small fast model is right.
CHAT_MODEL = _model("CHAT_MODEL", "groq/openai/gpt-oss-20b")

# Small structured jobs — finalising intake, applying FSD and package edits.
REPORT_MODEL = _model("REPORT_MODEL", "groq/openai/gpt-oss-120b")

# The BRD, its critique and its patch: the largest prose-shaped documents in
# the pipeline, and the screen plan that has to follow a schema exactly.
DETAILED_REPORT_MODEL = _model("DETAILED_REPORT_MODEL", "groq/openai/gpt-oss-120b")

# Screen sources. Ideally a code-specialised model — qwen3-coder produced the
# cleanest React of anything tried — but Groq serves no coder model on this
# account, so the large general model does both here. Kept as its own setting
# because the right answer differs from the screen plan's: a coder-tuned model
# failed that call outright, returning its own field names and dropping
# `purpose` and `keyElements` from every screen.
UI_MODEL = _model("UI_MODEL", "groq/openai/gpt-oss-120b")

# The screen plan is its own setting because it is its own kind of work, and
# two different models have now failed it while handling their own stage
# fine: qwen3-coder returned its own field names, and gpt-oss-120b dropped
# route, purpose and keyElements from every screen, twice, including on retry.
#
# It is also the cheapest call in the pipeline — roughly 1,700 completion
# tokens once per run, against ~90,000 for the screens themselves — so leaving
# it on a model that reliably follows a nested schema costs almost nothing
# even when the rest of the stage has moved elsewhere to save credits.
UI_PLAN_MODEL = _model("UI_PLAN_MODEL", "groq/openai/gpt-oss-120b")

# The largest schema in the pipeline — work items with dependency edges, plus a
# package per department carrying its own tech stack, owned data model, phased
# plan and security design. gpt-4o-mini could not hold it: one run returned
# four packages and zero work items, leaving an empty dependency graph the
# integrity check reported as clean, and the next dropped `securityDesign`
# from two packages outright.
DECOMPOSITION_MODEL = _model("DECOMPOSITION_MODEL", "groq/openai/gpt-oss-120b")

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Groq speaks the OpenAI chat-completions format too, so a model can be sent
# there instead by prefixing its id with "groq/". The prefix is stripped
# before the call — it is routing information for us, not part of the model
# name Groq knows.
GROQ_BASE_URL = "https://api.groq.com/openai/v1"
GROQ_PREFIX = "groq/"


def provider_for(model: str) -> tuple[str, str, str]:
    """Where to send this model: (base_url, api_key, model_id)."""
    if model.startswith(GROQ_PREFIX):
        return GROQ_BASE_URL, groq_api_key(), model[len(GROQ_PREFIX):]
    return OPENROUTER_BASE_URL, openrouter_api_key(), model


def groq_api_key() -> str:
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        raise RuntimeError("GROQ_API_KEY is not set")
    return key


def openrouter_api_key() -> str:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY is not set")
    return key


def mongodb_uri() -> str:
    return os.environ.get("MONGODB_URI", "mongodb://127.0.0.1:27017/ai_factory")
