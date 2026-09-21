"""Intake turn: NeMo / Presidio / Guardrails stand-ins on the same seams.

Production swaps these for the named products. The conversation must not reach
the model with raw personal data, and must not accept a shapeless reply.
"""

from __future__ import annotations

import json
import re
from typing import Any

EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
PHONE = re.compile(r"\b(?:\+?\d[\s-]?){8,}\b")
OFF_RAILS = re.compile(
    r"ignore (all|previous) instructions|you are now|jailbreak",
    re.I,
)


class InputRefused(Exception):
    """NeMo-shaped: the turn never reached the model."""


class OutputRefused(Exception):
    """Guardrails-shaped: the model replied, but the shape is unusable."""


def mask_personal_data(text: str) -> str:
    """Presidio stand-in — mask before anything leaves the tenant."""
    text = EMAIL.sub("<EMAIL>", text)
    return PHONE.sub("<PHONE>", text)


def screen_input(text: str) -> str:
    if not (text or "").strip():
        raise InputRefused("empty turn — the model is not asked to invent a question")
    if OFF_RAILS.search(text):
        raise InputRefused("input rails refused this turn as off-scope")
    return mask_personal_data(text.strip())


def parse_agent_output(raw: str) -> dict[str, Any]:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text.split("\n", 1)[-1]
        if text.endswith("```"):
            text = text[: -3]
        text = text.strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise OutputRefused("agent output is not JSON") from exc
    kind = data.get("type")
    if kind == "question":
        if not str(data.get("text") or "").strip():
            raise OutputRefused("question has no text")
        return data
    if kind == "scope_report":
        for key in ("in_scope", "out_of_scope", "success", "open_questions"):
            if key not in data:
                raise OutputRefused(f"scope report missing {key}")
        if not data["out_of_scope"]:
            raise OutputRefused("out of scope must not be empty on a real requirement")
        if not data["in_scope"]:
            raise OutputRefused("in scope is empty")
        return data
    raise OutputRefused(f"unknown agent output type {kind!r}")
