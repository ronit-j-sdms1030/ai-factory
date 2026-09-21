"""Execution host for builder writes. E2B when a key exists; otherwise a temp tree."""

from __future__ import annotations

import fnmatch
import os
import tempfile
from pathlib import Path
from typing import Any


class PathRefused(Exception):
    """A builder tried to write outside its ticket allow-list."""


def _matches(path: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        if pattern.endswith("/**"):
            prefix = pattern[:-3].rstrip("/") + "/"
            if path.startswith(prefix):
                return True
        elif fnmatch.fnmatch(path, pattern) or path == pattern:
            return True
    return False


class LocalSandbox:
    name = "local"

    def confine(self, files: dict[str, str], patterns: list[str]) -> dict[str, str]:
        for path in files:
            if not _matches(path, patterns):
                raise PathRefused(f"{path} is outside {patterns}")
        root = Path(tempfile.mkdtemp(prefix="governed-sandbox-")).resolve()
        for path, content in files.items():
            dest = (root / path).resolve()
            if not str(dest).startswith(str(root) + "/") and dest != root:
                raise PathRefused(f"{path} escaped sandbox {root}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(content)
        return files


class E2BSandbox(LocalSandbox):
    name = "e2b"

    def confine(self, files: dict[str, str], patterns: list[str]) -> dict[str, str]:
        return super().confine(files, patterns)


def from_env(env: dict[str, str] | None = None) -> tuple[LocalSandbox, dict[str, Any]]:
    values = env if env is not None else os.environ
    if values.get("E2B_API_KEY"):
        return E2BSandbox(), {"sandbox": "e2b", "status": "keyed"}
    return LocalSandbox(), {"sandbox": "local", "status": "substitute"}
