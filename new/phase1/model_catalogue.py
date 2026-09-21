"""Every OpenRouter model, plus notes on ones this factory has measured."""

from __future__ import annotations

import json
import threading
import time
from typing import Any
from urllib import error, request

_MODELS_URL = "https://openrouter.ai/api/v1/models"
_TTL_SECONDS = 3600
_lock = threading.Lock()
_cache: dict[str, Any] = {"at": 0.0, "models": []}

NOTES: dict[str, str] = {
    "anthropic/claude-haiku-4.5": "fast, strong at conversation",
    "openai/gpt-4o-mini": "cheap, reliable on small schemas",
    "openai/gpt-4o": "stronger on large schemas",
    "deepseek/deepseek-v3.2": "long technical documents",
    "qwen/qwen3-coder": "code-specialised",
    "google/gemini-2.0-flash-001": "fast, cheap vision+text",
    "meta-llama/llama-3.3-70b-instruct": "open-weight, strong general",
}


def _price_row(pricing: dict[str, Any]) -> tuple[str, float, float]:
    try:
        prompt = float(pricing.get("prompt") or 0) * 1e6
        completion = float(pricing.get("completion") or 0) * 1e6
    except (TypeError, ValueError):
        return "", 0.0, 0.0
    if not prompt and not completion:
        return "free", 0.0, 0.0
    return f"${prompt:.2f}/${completion:.2f} per M", prompt, completion


def _fetch() -> list[dict[str, Any]]:
    req = request.Request(_MODELS_URL, headers={"Accept": "application/json"})
    with request.urlopen(req, timeout=15) as response:
        payload = json.load(response)
    models = []
    for entry in payload.get("data") or []:
        model_id = entry.get("id")
        if not model_id:
            continue
        label, prompt_m, completion_m = _price_row(entry.get("pricing") or {})
        context = entry.get("context_length") or 0
        models.append(
            {
                "model": model_id,
                "name": entry.get("name") or model_id,
                "detail": " · ".join(
                    part
                    for part in (label, f"{context // 1000}k context" if context else "")
                    if part
                ),
                "prompt_per_million": prompt_m,
                "completion_per_million": completion_m,
            }
        )
    return sorted(models, key=lambda row: row["model"])


def catalogue() -> list[dict[str, Any]]:
    now = time.time()
    with _lock:
        if _cache["models"] and now - _cache["at"] < _TTL_SECONDS:
            return _cache["models"]
    try:
        models = _fetch()
    except (error.URLError, TimeoutError, ValueError, OSError):
        return list(_cache["models"])
    with _lock:
        _cache["at"], _cache["models"] = now, models
    return models


def choices(extra: list[str] | None = None) -> list[dict[str, Any]]:
    known = {row["model"]: row for row in catalogue()}
    measured = []
    for model_id, note in NOTES.items():
        row = known.get(model_id, {})
        measured.append(
            {
                "model": model_id,
                "name": row.get("name") or model_id,
                "detail": note,
                "note": note,
                "meta": row.get("detail") or "",
                "prompt_per_million": row.get("prompt_per_million") or 0.0,
                "completion_per_million": row.get("completion_per_million") or 0.0,
            }
        )
    seen = set(NOTES)
    rest = [
        {
            "model": row["model"],
            "name": row["name"],
            "detail": row["detail"],
            "note": "",
            "meta": row["detail"],
            "prompt_per_million": row.get("prompt_per_million") or 0.0,
            "completion_per_million": row.get("completion_per_million") or 0.0,
        }
        for row in known.values()
        if row["model"] not in seen
    ]
    extra_rows = [
        {
            "model": model_id,
            "name": model_id,
            "detail": "",
            "note": "",
            "meta": "",
            "prompt_per_million": 0.0,
            "completion_per_million": 0.0,
        }
        for model_id in extra or []
        if model_id not in seen and model_id not in known
    ]
    return measured + extra_rows + rest
