"""Context control — Repomix pack, CodeGraph, Serena edits, ACP swap.

Local substitutes. No vendor binary. Budget is an input, not a hope.
"""

from __future__ import annotations

import re
from typing import Any

TOKEN_BUDGET = 4000
_SYMBOL = re.compile(
    r"(?:export\s+)?(?:async\s+)?(?:function|class|const|def)\s+([A-Za-z_][\w]*)"
)
_IMPORT = re.compile(r"""(?:from |import |require\()['"]([^'"]+)['"]""")


def tokens(text: str) -> int:
    return max(1, len(text or "") // 4)


def pack(files: dict[str, str], budget: int = TOKEN_BUDGET) -> dict[str, Any]:
    packed: list[dict[str, Any]] = []
    omitted: list[str] = []
    used = 0
    for path, content in sorted(files.items()):
        cost = tokens(content)
        if packed and used + cost > budget:
            omitted.append(path)
            continue
        packed.append({"path": path, "tokens": cost, "content": content})
        used += cost
    return {
        "tool": "repomix",
        "status": "substitute",
        "budget": budget,
        "used": used,
        "files": packed,
        "omitted": omitted,
        "ok": used <= budget,
    }


def graph(files: dict[str, str]) -> dict[str, Any]:
    nodes: list[dict[str, str]] = []
    edges: list[dict[str, str]] = []
    for path, content in files.items():
        for match in _SYMBOL.finditer(content or ""):
            nodes.append({"path": path, "symbol": match.group(1)})
        for match in _IMPORT.finditer(content or ""):
            edges.append({"from": path, "to": match.group(1)})
    return {
        "tool": "codegraph",
        "status": "substitute",
        "tenant_local": True,
        "nodes": nodes,
        "edges": edges,
    }


def symbols(files: dict[str, str], name: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for path, content in files.items():
        for match in _SYMBOL.finditer(content or ""):
            if match.group(1) == name:
                found.append({"path": path, "symbol": name, "at": match.start()})
    return found


def edit_symbol(files: dict[str, str], name: str, replacement: str) -> dict[str, Any]:
    """Replace one function/class span. Whole-file rewrite is refused."""
    pattern = re.compile(
        rf"((?:export\s+)?(?:async\s+)?(?:function|class|const|def)\s+{re.escape(name)}\b[\s\S]*?)(?=\n(?:export\s+)?(?:async\s+)?(?:function|class|const|def)\s+|\Z)"
    )
    changed: dict[str, str] = dict(files)
    hits = 0
    for path, content in files.items():
        updated, n = pattern.subn(replacement.rstrip() + "\n", content, count=1)
        if n:
            changed[path] = updated
            hits += n
    return {
        "tool": "serena",
        "status": "substitute",
        "symbol": name,
        "hits": hits,
        "files": changed,
        "ok": hits > 0,
    }


def acp(engine: str) -> dict[str, Any]:
    return {
        "protocol": "acp",
        "engine": engine,
        "swappable": True,
        "status": "substitute",
        "note": "Agent Client Protocol seam — engine id is logged, not hard-wired",
    }


def assemble(files: dict[str, str], engine: str, budget: int = TOKEN_BUDGET) -> dict[str, Any]:
    packed = pack(files, budget)
    return {
        "pack": packed,
        "graph": graph({row["path"]: row["content"] for row in packed["files"]}),
        "acp": acp(engine),
        "budget": budget,
    }
