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
ALLOWED_COMPONENTS = (
    "Page",
    "Sidebar",
    "Button",
    "Field",
    "Table",
    "Card",
    "Badge",
    "Hero",
    "Image",
)
TOKEN_HINT = (
    "var(--color-bg), var(--color-sidebar), var(--color-surface), "
    "var(--color-text), var(--color-muted), var(--color-accent), "
    "var(--color-line), var(--space-md), var(--font-sans)"
)
PLACEHOLDER_RULE = (
    "Never emit angle-bracket placeholders such as <LOCATION>, <BRAND>, or <LOCATIONS>. "
    "Those are not components. Write the words as plain text (LOCATION). "
    "Only use real tags: Page, Sidebar, Button, Field, Table, Card, Badge, Hero, Image, "
    "and lowercase HTML."
)
COMPONENT_HINT = (
    "function Page, function Sidebar, function Button, function Field, "
    "function Table, function Card, function Badge, function Hero, function Image"
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
    "blush",
    "noir",
    "ocean",
    "sunrise",
    "plum",
)
THEME_ATTR = re.compile(r'data-theme=["\']([a-z]+)["\']')
THEME_HINT = (
    "data-theme midnight|aurora|paper|grove|coral|ink|glacier|sand|"
    "blush|noir|ocean|sunrise|plum"
)
HEX_COLOUR = re.compile(r"#[0-9a-fA-F]{3,8}\b")
# Hex OK only when assigning a design-token CSS variable (Studio colour pickers).
TOKEN_HEX = re.compile(
    r'--color-(?:bg|sidebar|surface|text|muted|accent|line)\s*["\']?\s*:\s*["\']?'
    r"#[0-9a-fA-F]{3,8}\b"
)
# All-caps tags such as <LOCATION> are intake placeholders, not components.
# Babel treats them as unclosed JSX and the live preview stays blank.
PLACEHOLDER_TAG = re.compile(r"</?([A-Z][A-Z0-9_]*)\b[^>]*?/?>")
MAX_RETRIES = 2


HOST_COMPONENTS = (
    "Page",
    "Sidebar",
    "Button",
    "Field",
    "Table",
    "Card",
    "Badge",
    "Hero",
    "Image",
)


def _top_level_function_spans(source: str, name: str) -> list[tuple[int, int]]:
    """Byte spans of top-level `function Name` bodies, including the keyword."""
    spans: list[tuple[int, int]] = []
    pattern = re.compile(rf"function\s+{re.escape(name)}\s*\(")
    depth = 0
    in_str: str | None = None
    i = 0
    text = source or ""
    while i < len(text):
        ch = text[i]
        if in_str:
            if ch == "\\":
                i += 2
                continue
            if ch == in_str:
                in_str = None
            i += 1
            continue
        if ch in {"'", '"', "`"}:
            in_str = ch
            i += 1
            continue
        if ch == "{":
            depth += 1
            i += 1
            continue
        if ch == "}":
            depth = max(0, depth - 1)
            i += 1
            continue
        if depth == 0:
            match = pattern.match(text, i)
            if match:
                brace = text.find("{", match.end())
                if brace < 0:
                    break
                inner = 0
                j = brace
                nested: str | None = None
                while j < len(text):
                    c = text[j]
                    if nested:
                        if c == "\\":
                            j += 2
                            continue
                        if c == nested:
                            nested = None
                        j += 1
                        continue
                    if c in {"'", '"', "`"}:
                        nested = c
                        j += 1
                        continue
                    if c == "{":
                        inner += 1
                    elif c == "}":
                        inner -= 1
                        if inner == 0:
                            spans.append((match.start(), j + 1))
                            i = j + 1
                            break
                    j += 1
                else:
                    break
                continue
        i += 1
    return spans


def dedupe_host_functions(source: str) -> str:
    """Keep one declaration of each design-system component.

    The host and the model both emit `function Sidebar`. Babel then stops the
    preview with 'Identifier Sidebar has already been declared'.
    The later declaration wins — that is the rewrite, not the stub prepended first.
    """
    text = source or ""
    for name in HOST_COMPONENTS:
        spans = _top_level_function_spans(text, name)
        if len(spans) < 2:
            continue
        for start, end in reversed(spans[:-1]):
            text = text[:start] + text[end:]
    return text


def neutralize_placeholder_tags(source: str) -> str:
    """Turn `<LOCATION>` into the text LOCATION. Leave PascalCase components."""

    def repl(match: re.Match[str]) -> str:
        if match.group(0).startswith("</"):
            return ""
        return match.group(1)

    return PLACEHOLDER_TAG.sub(repl, source or "")


class CompileFailed(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


_VOID_TAGS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
    "source", "track", "wbr",
})
_JSX_TAG = re.compile(r"<(/?)([A-Za-z][\w.]*)([^<>]*?)(/?)>")


def _jsx_tag_error(source: str) -> str | None:
    """Reject markup Babel cannot render. Comparisons with a space (`a < b`) are ignored."""
    stack: list[str] = []
    for match in _JSX_TAG.finditer(source or ""):
        closing, name, _attrs, self_close = match.groups()
        if self_close or name.lower() in _VOID_TAGS:
            if closing:
                return f"expected closing tag, found </{name}>"
            continue
        if closing:
            if not stack or stack[-1] != name:
                opened = stack[-1] if stack else name
                return f"expected closing tag for <{opened}>"
            stack.pop()
            continue
        stack.append(name)
    if stack:
        return f"expected closing tag for <{stack[-1]}>"
    return None


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
    if PLACEHOLDER_TAG.search(source or ""):
        raise CompileFailed("placeholder tag such as <LOCATION> — write plain text")
    tag_error = _jsx_tag_error(source)
    if tag_error:
        raise CompileFailed(tag_error)


def _has_banned_hex(source: str) -> bool:
    for match in HEX_COLOUR.finditer(source or ""):
        window = (source or "")[max(0, match.start() - 48) : match.end()]
        if TOKEN_HEX.search(window):
            continue
        return True
    return False


def conform(source: str, skill_text: str) -> list[str]:
    failures = []
    if _has_banned_hex(source):
        failures.append("raw hex colour; use design-system tokens or Studio token overrides")
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
        source = dedupe_host_functions(neutralize_placeholder_tags(produce(error)))
        try:
            compile_jsx(name, source)
            error = None
            break
        except CompileFailed as exc:
            error = exc.reason
    if error:
        raise CompileFailed(error)
    return source, conform(source, skill_text)
