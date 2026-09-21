"""Append-only token log for the demo usage dashboard."""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from phase1 import agent_settings, model_catalogue
from phase1.adapters.protocols import ModelCompletion

_LOCK = threading.Lock()
_FALLBACK = agent_settings._FALLBACK_MINI


def _path(root: Path) -> Path:
    return Path(root) / "usage.jsonl"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def estimate_tokens(messages: list[dict[str, str]], skill: str, text: str) -> tuple[int, int, int]:
    prompt_chars = len(skill or "") + sum(len(str(m.get("content") or "")) for m in messages)
    completion_chars = len(text or "")
    prompt = max(1, prompt_chars // 4)
    completion = max(1, completion_chars // 4)
    return prompt, completion, prompt + completion


def _rates(model: str) -> tuple[float, float]:
    priced = {row["model"]: row for row in model_catalogue.choices()}
    row = priced.get(model) or {}
    return (
        float(row.get("prompt_per_million") or _FALLBACK["prompt_per_million"]),
        float(row.get("completion_per_million") or _FALLBACK["completion_per_million"]),
    )


def cost_usd(model: str, prompt: int, completion: int) -> float:
    prompt_m, completion_m = _rates(model)
    return round(
        prompt * prompt_m / 1_000_000 + completion * completion_m / 1_000_000,
        6,
    )


def tokens_of(completion: ModelCompletion, messages: list[dict[str, str]], skill: str) -> tuple[int, int, int]:
    usage = (completion.metadata or {}).get("usage") or {}
    prompt = int(usage.get("prompt_tokens") or 0)
    comp = int(usage.get("completion_tokens") or 0)
    total = int(usage.get("total_tokens") or 0)
    if prompt or comp or total:
        if not total:
            total = prompt + comp
        if not prompt:
            prompt = max(0, total - comp)
        if not comp:
            comp = max(0, total - prompt)
        return prompt, comp, total
    return estimate_tokens(messages, skill, completion.text)


def record(
    root: Path,
    completion: ModelCompletion,
    messages: list[dict[str, str]],
    skill: str,
    *,
    agent: str = "",
) -> dict[str, Any]:
    prompt, comp, total = tokens_of(completion, messages, skill)
    row = {
        "at": _now(),
        "agent": agent or "intake",
        "model": completion.model,
        "prompt_tokens": prompt,
        "completion_tokens": comp,
        "total_tokens": total,
        "usd": cost_usd(completion.model, prompt, comp),
        "estimated": not bool((completion.metadata or {}).get("usage")),
    }
    path = _path(root)
    line = json.dumps(row, sort_keys=True) + "\n"
    with _LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
    return row


def _rows(root: Path) -> list[dict[str, Any]]:
    path = _path(root)
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def dashboard(root: Path) -> dict[str, Any]:
    rows = _rows(root)
    by_model: dict[str, dict[str, Any]] = {}
    prompt = completion = total = calls = 0
    usd = 0.0
    for row in rows:
        calls += 1
        prompt += int(row.get("prompt_tokens") or 0)
        completion += int(row.get("completion_tokens") or 0)
        total += int(row.get("total_tokens") or 0)
        usd += float(row.get("usd") or 0)
        model = str(row.get("model") or "unknown")
        bucket = by_model.setdefault(
            model,
            {"model": model, "calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "usd": 0.0},
        )
        bucket["calls"] += 1
        bucket["prompt_tokens"] += int(row.get("prompt_tokens") or 0)
        bucket["completion_tokens"] += int(row.get("completion_tokens") or 0)
        bucket["total_tokens"] += int(row.get("total_tokens") or 0)
        bucket["usd"] += float(row.get("usd") or 0)
    cycle = agent_settings.cycle_cost(root)
    recent = list(reversed(rows[-25:]))
    return {
        "recorded": {
            "calls": calls,
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "total_tokens": total,
            "usd": round(usd, 6),
            "currency": "USD",
        },
        "byModel": [
            {**bucket, "usd": round(bucket["usd"], 6)}
            for bucket in sorted(by_model.values(), key=lambda item: item["usd"], reverse=True)
        ],
        "recent": recent,
        "cycleEstimate": cycle,
    }
