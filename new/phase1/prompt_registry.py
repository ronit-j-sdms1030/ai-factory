"""Prompt Registry — versioned skill bundles with change history and approver.

Files on disk are the source. This module hashes them and keeps an append-only
history. Not a signed vendor registry UI.
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import skill_registry

_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _history_path(root: Path) -> Path:
    return Path(root) / "prompt_registry.jsonl"


def digest(text: str) -> str:
    return "sha256:" + hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:12]


def snapshot(agent: str, *, root: Path | None = None) -> dict[str, Any]:
    bundle = skill_registry.load_bundle(agent, root=root)
    return {
        "name": agent,
        "version": digest(bundle.content),
        "bundle_version": bundle.version,
        "files": [
            {
                "name": item.name,
                "path": item.path,
                "source": item.source,
                "version": digest(item.content),
            }
            for item in bundle.files
        ],
        "approver": "shipped",
    }


def _rows(root: Path) -> list[dict[str, Any]]:
    path = _history_path(root)
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


def history(root: Path, name: str = "") -> list[dict[str, Any]]:
    rows = _rows(root)
    if name:
        rows = [row for row in rows if row.get("name") == name]
    return list(reversed(rows))


def record(root: Path, agent: str, actor_id: str) -> dict[str, Any]:
    snap = snapshot(agent, root=root)
    row = {**snap, "at": _now(), "approver": actor_id or "shipped"}
    line = json.dumps(row, sort_keys=True) + "\n"
    with _LOCK:
        path = _history_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line)
    return row


def inventory(root: Path | None = None) -> list[dict[str, Any]]:
    rows = []
    for agent in skill_registry.BUNDLES:
        try:
            snap = snapshot(agent, root=root)
        except (KeyError, skill_registry.IncompleteSkillFile, OSError):
            continue
        hist = history(Path(root), agent) if root is not None else []
        if hist:
            snap["approver"] = hist[0].get("approver") or "shipped"
            snap["history"] = hist[:10]
        else:
            snap["history"] = []
        rows.append(snap)
    return rows
