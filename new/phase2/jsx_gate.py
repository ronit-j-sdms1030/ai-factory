"""Compile and conform gates for one generated screen.

A truncation costs one screen rather than the whole design. The retry hands
the model its own parser error. Nothing unrenderable reaches a reviewer.
"""

from __future__ import annotations

import re
from typing import Callable

from pathlib import Path

DESIGN_SYSTEM_PATH = Path("skills/design-system.skill.md")

ALLOWED_TOKENS = (
    "--color-bg",
    "--color-sidebar",
    "--color-surface",
    "--color-text",
    "--color-muted",
    "--color-accent",
    "--color-line",
    "--space-md",
    "--font-sans",
)
ALLOWED_COMPONENTS = ("Page", "Sidebar", "Button", "Field", "Table")
TOKEN_HINT = (
    "var(--color-bg), var(--color-sidebar), var(--color-surface), "
    "var(--color-text), var(--color-muted), var(--color-accent), "
    "var(--color-line), var(--space-md), var(--font-sans)"
)
COMPONENT_HINT = (
    "function Page, function Sidebar, function Button, function Field, "
    "function Table"
)
ALLOWED_THEMES = (
    "midnight",
    "aurora",
    "paper",
    "grove",
    "coral",
    "ink",
    "glacier",
    "sand",
)
THEME_ATTR = re.compile(r'data-theme=["\']([a-z]+)["\']')
THEME_HINT = "data-theme midnight|aurora|paper|grove|coral|ink|glacier|sand"
HEX_COLOUR = re.compile(r"#[0-9a-fA-F]{3,8}\b")
MAX_RETRIES = 2


class CompileFailed(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def compile_jsx(name: str, source: str) -> None:
    named = (
        re.search(rf"function\s+{re.escape(name)}\b", source) is not None
        or re.search(rf"(?:const|let|var)\s+{re.escape(name)}\s*=", source) is not None
        or re.search(rf"export\s+default\s+function\s+{re.escape(name)}\b", source) is not None
    )
    if not named:
        raise CompileFailed(f"no component called {name}")
    if source.count("{") != source.count("}"):
        raise CompileFailed("unbalanced braces")
    if source.count("(") != source.count(")"):
        raise CompileFailed("unbalanced parentheses")
    if "return (" not in source and "return(" not in source:
        raise CompileFailed("component does not return markup")
    if "<" not in source:
        raise CompileFailed("no JSX markup")


def conform(source: str, skill_text: str) -> list[str]:
    failures = []
    if HEX_COLOUR.search(source):
        failures.append("raw hex colour; use design-system tokens")
    for found in THEME_ATTR.findall(source):
        if found not in ALLOWED_THEMES:
            failures.append(f"unknown theme {found}; use a named palette")
    if not any(token in source for token in ALLOWED_TOKENS):
        failures.append("no design-system token used")
    if "var(--color-" not in source and "--color-" not in skill_text:
        failures.append("colour tokens missing from skill file")
    if any(name not in skill_text for name in ALLOWED_COMPONENTS):
        failures.append("design-system skill file is incomplete")
    if "function Page" not in source:
        failures.append("screen must use the Page component")
    if "function Button" not in source and "<Button" not in source:
        failures.append("screen must use the Button component")
    if "<h1" not in source:
        failures.append("one h1 required")
    return failures


def generate_with_gate(
    name: str,
    produce: Callable[[str | None], str],
    skill_text: str,
) -> tuple[str, list[str]]:
    error: str | None = None
    source = ""
    for _ in range(MAX_RETRIES + 1):
        source = produce(error)
        try:
            compile_jsx(name, source)
            error = None
            break
        except CompileFailed as exc:
            error = exc.reason
    if error:
        raise CompileFailed(error)
    return source, conform(source, skill_text)
