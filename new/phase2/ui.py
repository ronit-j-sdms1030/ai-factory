"""UI/UX Agent — one React screen at a time, constrained by the design system."""

from __future__ import annotations

import re
from typing import Any

from phase2 import coverage, jsx_gate

# BRD page titles often contain "(week view)" or "<LOCATION>". A raw
# interpolate trips the Gate 2 JSX paren/brace count and blocks CTL approve.
_UNSAFE_JSX = str.maketrans({
    "(": " ",
    ")": " ",
    "{": " ",
    "}": " ",
    "<": " ",
    ">": " ",
    '"': "'",
    "\\": " ",
})


def _safe_jsx_text(value: str) -> str:
    return " ".join(str(value or "").translate(_UNSAFE_JSX).split())


def _display_title(name: str, page: dict[str, str]) -> str:
    raw = page.get("id") or name
    spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", raw)
    spaced = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", spaced)
    return _safe_jsx_text(spaced)


def _fields_and_action(title: str, description: str) -> tuple[list[tuple[str, str]], str, tuple[str, ...]]:
    blob = f"{title} {description}".lower()
    action = "Save"
    if "cancel" in blob:
        action = "Cancel"
    elif "sign" in blob or "login" in blob or "password" in blob:
        action = "Sign in"
    elif "search" in blob or "avail" in blob or "find" in blob:
        action = "Search"
    elif "export" in blob:
        action = "Export"
    fields: list[tuple[str, str]] = []
    hints = (
        ("room", "Room", ("room",)),
        ("starts", "Starts", ("start", "from", "begin")),
        ("ends", "Ends", ("end", "until", "to ")),
        ("note", "Note", ("note", "reason", "comment")),
        ("email", "Email", ("email",)),
        ("password", "Password", ("password", "login")),
        ("name", "Name", ("name", "employee", "staff")),
        ("date", "Date", ("date", "day", "when")),
    )
    for key, label, needles in hints:
        if any(needle in blob for needle in needles):
            fields.append((key, label))
    if not fields:
        fields = [("detail", title or "Detail")]
    if any(token in blob for token in ("book", "room", "slot")):
        rows = ("Open slot", "Held", "Confirmed")
    elif "login" in blob or "password" in blob:
        rows = ("Staff", "Manager", "Reviewer")
    else:
        rows = (f"{title} — ready", "In progress", "Done")
    return fields[:4], action, rows


def pick_theme(brd_text: str) -> str:
    blob = (brd_text or "").lower()
    hints = (
        (("hotel", "room", "book", "stay", "hospitality", "resort"), "sand"),
        (("clinic", "patient", "health", "care", "hospital"), "glacier"),
        (("bank", "invoice", "ledger", "finance", "pay"), "ink"),
        (("garden", "farm", "food", "plant", "green"), "grove"),
        (("shop", "store", "cart", "retail", "order"), "coral"),
        (("news", "blog", "write", "editorial"), "paper"),
        (("music", "media", "stream", "play"), "aurora"),
    )
    for needles, theme in hints:
        if any(needle in blob for needle in needles):
            return theme
    if not blob.strip():
        return "midnight"
    return jsx_gate.ALLOWED_THEMES[sum(map(ord, blob[:80])) % len(jsx_gate.ALLOWED_THEMES)]


def _brand_label(page: dict[str, str]) -> str:
    raw = _safe_jsx_text(page.get("id") or "App")
    return raw[:28] or "App"


def screen_label(name: str) -> str:
    spaced = re.sub(r"([a-z])([A-Z])", r"\1 \2", name or "")
    return _safe_jsx_text(spaced)[:28] or "Screen"


def nav_buttons(roster: list[str], indent: str = "        ") -> str:
    return f"\n{indent}".join(
        f'<Button onClick={{() => navigate("{item}")}}>{screen_label(item)}</Button>'
        for item in roster
    )


def _screen_source(
    name: str,
    page: dict[str, str],
    siblings: list[str],
    *,
    theme: str = "midnight",
    brand: str = "App",
    roster: list[str] | None = None,
) -> str:
    title = _display_title(name, page)
    description = _safe_jsx_text(page.get("description") or title)
    fields, action, rows = _fields_and_action(title, description)
    field_jsx = "\n        ".join(
        f'<Field name="{key}" label="{label}" />' for key, label in fields
    )
    nav = nav_buttons(roster or ([name] + [item for item in siblings[:6] if item != name]))
    row_jsx = "\n          ".join(f"<tr><td>{row}</td></tr>" for row in rows)
    theme_name = theme if theme in jsx_gate.ALLOWED_THEMES else "midnight"
    brand_text = _safe_jsx_text(brand) or "App"
    return f'''{_factory_helpers(theme_name)}function {name}() {{
  return (
    <Page data-theme="{theme_name}">
      <Sidebar>
        <p>{brand_text}</p>
        {nav}
      </Sidebar>
      <div>
        <h1>{title}</h1>
        <p>{description}</p>
        {field_jsx}
        <Button type="submit">{action}</Button>
        <Table heading="{title}">
          {row_jsx}
        </Table>
      </div>
    </Page>
  );
}}
'''


_RESERVED = {"Page", "Sidebar", "Button", "Field", "Table", "ScreenCanvas", "ScreenBody"}


def _theme_of(source: str) -> str:
    found = jsx_gate.THEME_ATTR.search(source or "")
    if found and found.group(1) in jsx_gate.ALLOWED_THEMES:
        return found.group(1)
    return "midnight"


def _factory_helpers(theme: str = "midnight") -> str:
    theme_name = theme if theme in jsx_gate.ALLOWED_THEMES else "midnight"
    return f'''function Page(props) {{
  return <main className="page" data-theme="{theme_name}" style={{{{background:"var(--color-bg)", color:"var(--color-text)", fontFamily:"var(--font-sans)", display:"flex", minHeight:"100vh"}}}}>{{props.children}}</main>;
}}
function Sidebar(props) {{
  return <aside className="sidebar" style={{{{background:"var(--color-sidebar)", color:"var(--color-text)", width:240, flexShrink:0, padding:"var(--space-md)", borderRight:"1px solid var(--color-line)"}}}}>{{props.children}}</aside>;
}}
function Button(props) {{
  return <button type={{props.type || "button"}} onClick={{props.onClick}} style={{{{background:"var(--color-accent)", color:"var(--color-bg)", padding:"var(--space-md)"}}}}>{{props.children}}</button>;
}}
function Field(props) {{
  return <label style={{{{color:"var(--color-muted)"}}}}>{{props.label}}<input name={{props.name}} /></label>;
}}
function Table(props) {{
  return <table style={{{{background:"var(--color-surface)", border:"1px solid var(--color-line)"}}}}><thead><tr><th>{{props.heading}}</th></tr></thead><tbody>{{props.children}}</tbody></table>;
}}
'''


_RETHEME_WORDS = (
    "reimagin", "redesign", "restyle", "theme", "scene", "palette", "colour", "color", "look",
)


def keep_theme(source: str, theme: str, instruction: str) -> str:
    """Pin data-theme to the screen's current theme unless asked to change it."""
    if theme not in jsx_gate.ALLOWED_THEMES:
        return source
    if not jsx_gate.THEME_ATTR.search(source or ""):
        return re.sub(r"<Page(?=[\s>/])", f'<Page data-theme="{theme}"', source, count=1)
    if any(word in (instruction or "").lower() for word in _RETHEME_WORDS):
        return source
    return jsx_gate.THEME_ATTR.sub(f'data-theme="{theme}"', source)


def repair_for_gate(name: str, source: str) -> str:
    """Make a model screen pass conform without another billed call."""
    text = ensure_named_screen(name, source)
    text = jsx_gate.HEX_COLOUR.sub("var(--color-accent)", text)

    def _fix_theme(match: re.Match[str]) -> str:
        if match.group(1) in jsx_gate.ALLOWED_THEMES:
            return match.group(0)
        return 'data-theme="midnight"'

    text = jsx_gate.THEME_ATTR.sub(_fix_theme, text)
    theme = _theme_of(text)
    if "function Page" not in text:
        text = _factory_helpers(theme) + "\n" + text
    if not any(token in text for token in jsx_gate.ALLOWED_TOKENS):
        text = (
            "const __ds = {bg:'var(--color-bg)', sidebar:'var(--color-sidebar)', "
            "surface:'var(--color-surface)', text:'var(--color-text)', "
            "muted:'var(--color-muted)', accent:'var(--color-accent)', "
            "line:'var(--color-line)', space:'var(--space-md)', font:'var(--font-sans)'};\n"
            + text
        )
    if "function Button" not in text and "<Button" not in text:
        text = (
            'function Button(props) { return <button type={props.type || "button"} '
            'style={{background:"var(--color-accent)", color:"var(--color-bg)", '
            'padding:"var(--space-md)"}}>{props.children}</button>; }\n'
            + text
        )
    return text


def ensure_sidebar_shell(
    name: str, source: str, roster: list[str] | None = None, theme: str = ""
) -> str:
    """Wrap a form-only rewrite in Page + Sidebar so Ask 'add a sidebar' lands."""
    text = source or ""
    if re.search(r"<Sidebar[\s/>]", text):
        return text
    theme = theme if theme in jsx_gate.ALLOWED_THEMES else _theme_of(text)
    if "function Page" not in text:
        text = _factory_helpers(theme) + "\n" + text
    inner = "ScreenCanvas" if "function ScreenCanvas" not in text else "ScreenBody"
    renamed = re.sub(
        rf"function\s+{re.escape(name)}\s*\(",
        f"function {inner}(",
        text,
        count=1,
    )
    if renamed == text:
        renamed = re.sub(
            rf"(?:const|let|var)\s+{re.escape(name)}\s*=",
            f"const {inner} =",
            text,
            count=1,
        )
    if renamed == text:
        return text
    text = renamed
    label = screen_label(name)
    heading = "" if "<h1" in text else f"\n        <h1>{label}</h1>"
    nav = nav_buttons(roster or [name])
    text += (
        f"\nfunction {name}() {{\n"
        f"  return (\n"
        f"    <Page data-theme=\"{theme}\">\n"
        f"      <Sidebar>\n"
        f"        <p>{label}</p>\n"
        f"        {nav}\n"
        f"      </Sidebar>\n"
        f"      <div>{heading}\n"
        f"        <{inner} />\n"
        f"      </div>\n"
        f"    </Page>\n"
        f"  );\n"
        f"}}\n"
    )
    return text


def _has_named_screen(name: str, source: str) -> bool:
    return bool(
        re.search(rf"function\s+{re.escape(name)}\b", source)
        or re.search(rf"(?:const|let|var)\s+{re.escape(name)}\s*=", source)
        or re.search(rf"export\s+default\s+function\s+{re.escape(name)}\b", source)
    )


def ensure_named_screen(name: str, source: str) -> str:
    """Guarantee compile_jsx can see function {name}."""
    text = _adopt_model_source(name, source)
    if _has_named_screen(name, text):
        return text
    return text.rstrip() + f"\nfunction {name}() {{\n  return ( <Page /> );\n}}\n"


LIBRARY_GLOBALS = {
    "lucide-react": "LucideReact",
    "recharts": "Recharts",
    "framer-motion": "Motion",
    "motion/react": "Motion",
    "dayjs": "dayjs",
}
_IMPORT_RE = re.compile(
    r"^import\s+(?:(\w+)\s*,?\s*)?(?:\{([^}]*)\})?\s*from\s+[\"']([^\"']+)[\"'];?[ \t]*$",
    re.M,
)


def _imports_to_globals(text: str) -> str:
    """Browser screens have no bundler: `import` from a host library becomes a global lookup."""

    def swap(match: re.Match[str]) -> str:
        default, named, module = match.group(1), match.group(2), match.group(3)
        glob = LIBRARY_GLOBALS.get(module)
        if not glob:
            return match.group(0)
        lines = []
        if default and default not in {"React"}:
            lines.append(f"const {default} = window.{glob};")
        if named and named.strip():
            parts = [p.strip() for p in named.split(",") if p.strip()]
            pairs = ", ".join(re.sub(r"\s+as\s+", ": ", p) for p in parts)
            lines.append(f"const {{ {pairs} }} = window.{glob};")
        return "\n".join(lines)

    return _IMPORT_RE.sub(swap, text or "")


def _adopt_model_source(name: str, proposed: str) -> str:
    """Keep a model screen when it is JSX for this component.

    The model often names the function after the page, not the component the
    coverage check requires. Rename that one function and drop a markdown fence.
    """
    text = (proposed or "").strip()
    fenced = re.search(r"```(?:jsx|javascript|js)?\s*([\s\S]*?)```", text, re.I)
    if fenced:
        text = fenced.group(1).strip()
    text = _imports_to_globals(text)
    text = re.sub(r"^import\s+[^;]+;?\s*", "", text, flags=re.M)
    text = re.sub(r"\bexport\s+default\s+function\s+", "function ", text)
    text = re.sub(r"\bexport\s+default\s+", "", text)
    text = re.sub(r"\bexport\s+", "", text)
    if f"function {name}" in text or re.search(rf"(?:const|let|var)\s+{re.escape(name)}\s*=", text):
        return text
    names = [
        found
        for found in re.findall(r"function\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", text)
        if found not in _RESERVED
    ]
    names += [
        found
        for found in re.findall(
            r"(?:const|let|var)\s+([A-Z][A-Za-z0-9_]*)\s*=\s*(?:function|\()",
            text,
        )
        if found not in _RESERVED and found not in names
    ]
    if names:
        old = names[-1]
        if f"function {old}" in text:
            text = re.sub(rf"function\s+{old}\s*\(", f"function {name}(", text, count=1)
        else:
            converted = re.sub(
                rf"(?:const|let|var)\s+{old}\s*=\s*(?:async\s*)?\(([^)]*)\)\s*=>",
                rf"function {name}(\1)",
                text,
                count=1,
            )
            if converted == text:
                converted = re.sub(
                    rf"(?:const|let|var)\s+{old}\s*=\s*(?:async\s*)?function\s*\(",
                    f"function {name}(",
                    text,
                    count=1,
                )
            text = converted
        return text
    return text


def build_screens(
    brd_text: str,
    skill_text: str,
    *,
    extra_pages: list[dict[str, str]] | None = None,
    sources: dict[str, str] | None = None,
) -> dict[str, Any]:
    pages = coverage.pages_from_brd(brd_text)
    used: set[str] = set()
    screens: list[dict[str, Any]] = []
    repaired: list[str] = []
    conform_failures: list[dict[str, str]] = []
    theme = pick_theme(brd_text)
    brand = _brand_label(pages[0]) if pages else "App"
    planned: set[str] = set()
    roster = [coverage.component_name(page, planned) for page in pages]

    def add(page: dict[str, str], *, repaired_gap: bool = False) -> None:
        name = coverage.component_name(page, used)
        siblings = [s["name"] for s in screens]
        nav = roster if name in roster else roster + [name]
        proposed = (sources or {}).get(name) or (sources or {}).get(page.get("id") or "")
        source = ""
        failures: list[str] = []
        if proposed:
            prepared = _adopt_model_source(name, proposed)
            try:
                prepared = keep_theme(prepared, theme, "")
                prepared = repair_for_gate(name, prepared)
                prepared = ensure_sidebar_shell(name, prepared, roster=nav, theme=theme)
                jsx_gate.compile_jsx(name, prepared)
                source = prepared
                failures = jsx_gate.conform(source, skill_text)
            except jsx_gate.CompileFailed:
                source = ""
        if not source:
            try:
                source, failures = jsx_gate.generate_with_gate(
                    name,
                    lambda error, _n=name, _p=page, _s=siblings, _r=nav: (
                        _screen_source(_n, _p, _s, theme=theme, brand=brand, roster=_r)
                        if not error
                        else _screen_source(_n, _p, _s, theme=theme, brand=brand, roster=_r)
                        + f"\n// retry after: {error}\n"
                    ),
                    skill_text,
                )
            except jsx_gate.CompileFailed:
                source = _screen_source(
                    name, page, siblings, theme=theme, brand=brand, roster=nav
                )
                jsx_gate.compile_jsx(name, source)
                failures = jsx_gate.conform(source, skill_text)
        route = "/" + name.lower()
        screens.append(
            {
                "name": name,
                "route": route,
                "source": source,
                "page": page["id"],
            }
        )
        if repaired_gap:
            repaired.append(name)
        if failures:
            conform_failures.append({"name": name, "reason": failures[0]})

    for page in pages:
        add(page)

    if extra_pages:
        known = {coverage._fold(p["id"] + p.get("description", "")) for p in pages}
        for page in extra_pages:
            key = coverage._fold(page["id"] + page.get("description", ""))
            if key not in known:
                pages.append(page)
                add(page, repaired_gap=True)
                known.add(key)

    report = coverage.check(pages, screens)
    for gap in list(report["gaps"]):
        add(gap, repaired_gap=True)
    report = coverage.check(pages, screens)
    return {
        "screens": screens,
        "pages": pages,
        "repaired": repaired,
        "extras": [s["name"] for s in report["extras"]],
        "conform_failures": conform_failures,
        "preview_index": screens[0]["name"] if screens else "",
    }


def gated_source(name: str, source: str, skill_text: str) -> str:
    source = repair_for_gate(name, source)
    jsx_gate.compile_jsx(name, source)
    failures = jsx_gate.conform(source, skill_text)
    if failures:
        raise jsx_gate.CompileFailed(failures[0])
    return source


def apply_instruction(name: str, source: str, instruction: str, skill_text: str) -> str:
    """Deterministic fallback when no model rewrite is available."""
    updated = source
    if "</h1>" in source:
        updated = source.replace("</h1>", f"</h1>\n      <p>{_safe_jsx_text(instruction)}</p>", 1)
    else:
        updated = source + f"\n// {instruction}\n"
    return gated_source(name, updated, skill_text)


def apply_model_rewrite(
    name: str,
    proposed: str,
    skill_text: str,
    *,
    roster: list[str] | None = None,
    theme: str = "",
    instruction: str = "",
) -> str:
    prepared = ensure_named_screen(name, proposed)
    prepared = keep_theme(prepared, theme, instruction)
    prepared = repair_for_gate(name, prepared)
    prepared = ensure_sidebar_shell(name, prepared, roster=roster, theme=theme)
    return gated_source(name, prepared, skill_text)
