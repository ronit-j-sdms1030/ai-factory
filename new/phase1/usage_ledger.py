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
    if model_catalogue.is_free(model):
        return 0.0, 0.0
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
    requirement_id: str = "",
) -> dict[str, Any]:
    prompt, comp, total = tokens_of(completion, messages, skill)
    row = {
        "at": _now(),
        "agent": agent or "intake",
        "requirement_id": requirement_id or "",
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


def _bucket(store: dict[str, dict[str, Any]], key: str, label: str) -> dict[str, Any]:
    return store.setdefault(
        key,
        {
            label: key,
            "calls": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "usd": 0.0,
        },
    )


def _add(bucket: dict[str, Any], row: dict[str, Any]) -> None:
    bucket["calls"] += 1
    bucket["prompt_tokens"] += int(row.get("prompt_tokens") or 0)
    bucket["completion_tokens"] += int(row.get("completion_tokens") or 0)
    bucket["total_tokens"] += int(row.get("total_tokens") or 0)
    bucket["usd"] += float(row.get("usd") or 0)


def _governance(root: Path, store: Any = None) -> dict[str, Any]:
    runs = []
    events = []
    if store is not None:
        runs = list(store.all() or [])
        events = list(store.events() if hasattr(store, "events") else [])
    decisions = [row for row in events if row.get("kind") == "decision"]
    approved = [
        row
        for row in decisions
        if (row.get("body") or {}).get("outcome") == "approve"
        and (row.get("body") or {}).get("satisfied")
    ]
    revise = [row for row in decisions if (row.get("body") or {}).get("outcome") == "revise"]
    complete = [
        row
        for row in runs
        if ((row.get("requirement") or {}).get("phase") == "complete")
    ]
    by_req = _rows(root)
    spend: dict[str, float] = {}
    for row in by_req:
        rid = str(row.get("requirement_id") or "").strip()
        if not rid:
            continue
        spend[rid] = spend.get(rid, 0.0) + float(row.get("usd") or 0)
    merged_cost = [spend[rid] for rid in spend if any(
        ((item.get("requirement") or {}).get("id") == rid)
        and ((item.get("requirement") or {}).get("phase") == "complete")
        for item in runs
    )]
    densities = []
    for item in runs:
        board = (item.get("build") or {}).get("findings") or {}
        if board.get("density_per_kloc") is not None:
            densities.append(float(board["density_per_kloc"]))
    return {
        "source": "usage_ledger",
        "status": "substitute",
        "note": "Langfuse viewer is not live; metrics are local JSONL + the run store",
        "gateAcceptRate": (
            round(len(approved) / len(decisions), 3) if decisions else None
        ),
        "reworkRate": round(len(revise) / max(len(decisions), 1), 3) if decisions else None,
        "costPerMergedChange": (
            round(sum(merged_cost) / len(merged_cost), 6) if merged_cost else None
        ),
        "findingDensity": (
            round(sum(densities) / len(densities), 2) if densities else None
        ),
        "completed": len(complete),
    }


def dashboard(root: Path, store: Any = None) -> dict[str, Any]:
    rows = _rows(root)
    by_model: dict[str, dict[str, Any]] = {}
    by_agent: dict[str, dict[str, Any]] = {}
    by_requirement: dict[str, dict[str, Any]] = {}
    prompt = completion = total = calls = 0
    usd = 0.0
    for row in rows:
        calls += 1
        prompt += int(row.get("prompt_tokens") or 0)
        completion += int(row.get("completion_tokens") or 0)
        total += int(row.get("total_tokens") or 0)
        usd += float(row.get("usd") or 0)
        _add(_bucket(by_model, str(row.get("model") or "unknown"), "model"), row)
        _add(_bucket(by_agent, str(row.get("agent") or "unknown"), "agent"), row)
        rid = str(row.get("requirement_id") or "").strip() or "(no requirement)"
        _add(_bucket(by_requirement, rid, "requirementId"), row)
    cycle = agent_settings.cycle_cost(root)
    history = list(reversed(rows[-200:]))
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
        "byAgent": [
            {**bucket, "usd": round(bucket["usd"], 6)}
            for bucket in sorted(by_agent.values(), key=lambda item: item["usd"], reverse=True)
        ],
        "byRequirement": [
            {**bucket, "usd": round(bucket["usd"], 6)}
            for bucket in sorted(by_requirement.values(), key=lambda item: item["usd"], reverse=True)
        ],
        "recent": history[:25],
        "history": history,
        "cycleEstimate": cycle,
        "governance": _governance(root, store),
    }
