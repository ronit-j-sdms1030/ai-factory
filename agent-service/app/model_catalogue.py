"""Every model OpenRouter serves, plus notes on the ones we have measured.

A curated list of nine was easy to reason about and wrong in practice: the
right model for a stage is discovered by running it, and being unable to try
one without a code change means it does not get tried. OpenRouter publishes
its whole catalogue, so the picker offers all of it.

The hand-written notes stay on top. "Code-specialised, best measured for
screens" is knowledge this project paid for — eleven of twelve screens
rendering against four — and no generated description carries it. Models we
have not used get their vendor description, price and context window, which is
enough to choose between them.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.request
from typing import Any

log = logging.getLogger(__name__)

_MODELS_URL = "https://openrouter.ai/api/v1/models"

# Refetched hourly. New models appear often enough to matter and rarely enough
# that a stale hour costs nothing.
_TTL_SECONDS = 3600

_lock = threading.Lock()
_cache: dict[str, Any] = {"at": 0.0, "models": []}

# What this project has learned about specific models, by using them. Ordered:
# these appear first in the picker, ahead of the full catalogue.
NOTES: dict[str, str] = {
    "anthropic/claude-haiku-4.5": "fast, strong at conversation",
    "openai/gpt-4o-mini": "cheap, reliable on small schemas",
    "openai/gpt-4o": "stronger on large schemas",
    "deepseek/deepseek-v3.2:nitro": "long technical documents, fastest route",
    "deepseek/deepseek-v3.2": "same model, standard routing",
    "qwen/qwen3-coder": "code-specialised, best measured for screens",
    "qwen/qwen3-coder-30b-a3b-instruct": "cheapest code-specialised option",
    "groq/openai/gpt-oss-120b": "free tier, 8k tokens/minute ceiling",
    "groq/openai/gpt-oss-20b": "free tier, smaller",
}


def _price(pricing: dict[str, Any]) -> str:
    """Per-million prompt/completion, which is how these are compared."""
    try:
        prompt = float(pricing.get("prompt") or 0) * 1e6
        completion = float(pricing.get("completion") or 0) * 1e6
    except (TypeError, ValueError):
        return ""
    if not prompt and not completion:
        return "free"
    return f"${prompt:.2f}/${completion:.2f} per M"


def _fetch() -> list[dict[str, str]]:
    request = urllib.request.Request(_MODELS_URL, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=15) as response:
        payload = json.load(response)

    models = []
    for entry in payload.get("data") or []:
        model_id = entry.get("id")
        if not model_id:
            continue
        context = entry.get("context_length") or 0
        models.append(
            {
                "model": model_id,
                "name": entry.get("name") or model_id,
                "detail": " · ".join(
                    part for part in (_price(entry.get("pricing") or {}),
                                      f"{context // 1000}k context" if context else "")
                    if part
                ),
            }
        )
    return sorted(models, key=lambda m: m["model"])


def _openrouter_models() -> list[dict[str, str]]:
    """Cached catalogue. Returns whatever it last had if a refetch fails."""
    now = time.time()
    with _lock:
        fresh = _cache["models"] and now - _cache["at"] < _TTL_SECONDS
        if fresh:
            return _cache["models"]

    try:
        models = _fetch()
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        # A picker that cannot list every model is a smaller problem than a
        # settings page that will not load, so the stale copy is kept and the
        # measured entries below still work.
        log.warning("could not refresh the OpenRouter catalogue: %s", exc)
        return _cache["models"]

    with _lock:
        _cache["at"], _cache["models"] = now, models
    return models


def choices(extra: list[str] | None = None) -> list[dict[str, str]]:
    """The picker's contents: measured models first, then everything else."""
    catalogue = _openrouter_models()
    by_id = {m["model"]: m for m in catalogue}

    measured = []
    for model_id, note in NOTES.items():
        known = by_id.get(model_id, {})
        measured.append(
            {
                "model": model_id,
                "name": known.get("name") or model_id,
                "detail": note,
                "note": note,
                "meta": known.get("detail", ""),
            }
        )

    seen = set(NOTES)
    rest = [
        {"model": m["model"], "name": m["name"], "detail": m["detail"], "note": "", "meta": m["detail"]}
        for m in catalogue
        if m["model"] not in seen
    ]

    # Anything named in MODEL_CHOICES that the catalogue does not carry — a
    # provider-prefixed id, or a model added faster than this list refreshes.
    known_ids = seen | {m["model"] for m in rest}
    additional = [
        {"model": model_id, "name": model_id, "detail": "", "note": "", "meta": ""}
        for model_id in (extra or [])
        if model_id not in known_ids
    ]

    return measured + additional + rest
