"""UI/UX Agent — one React screen at a time, constrained by the design system."""

from __future__ import annotations

from typing import Any

from phase2 import coverage, jsx_gate


def _screen_source(name: str, page: dict[str, str], siblings: list[str]) -> str:
    roster = ", ".join(siblings) if siblings else "none yet"
    title = page.get("id") or name
    description = page.get("description") or title
    return f'''function Page(props) {{
  return <main className="page" style={{{{background:"var(--color-bg)", color:"var(--color-text)", fontFamily:"var(--font-sans)", padding:"var(--space-md)"}}}}>{{props.children}}</main>;
}}
function Button(props) {{
  return <button type={{props.type || "button"}} style={{{{background:"var(--color-accent)", color:"var(--color-bg)", padding:"var(--space-md)"}}}}>{{props.children}}</button>;
}}
function Field(props) {{
  return <label>{{props.label}}<input name={{props.name}} /></label>;
}}
function Table(props) {{
  return <table><thead><tr><th>{{props.heading}}</th></tr></thead><tbody>{{props.children}}</tbody></table>;
}}
function {name}() {{
  return (
    <Page>
      <h1>{title}</h1>
      <p>{description}</p>
      <p>Sibling screens: {roster}</p>
      <Field name="query" label="{title} input" />
      <Button type="submit">Continue</Button>
      <Table heading="{title}"><tr><td>Ready for Gate 3 preview.</td></tr></Table>
    </Page>
  );
}}
'''


def build_screens(
    brd_text: str,
    skill_text: str,
    *,
    extra_pages: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    pages = coverage.pages_from_brd(brd_text)
    used: set[str] = set()
    screens: list[dict[str, Any]] = []
    repaired: list[str] = []
    conform_failures: list[dict[str, str]] = []

    def add(page: dict[str, str], *, repaired_gap: bool = False) -> None:
        name = coverage.component_name(page, used)
        siblings = [s["name"] for s in screens]
        source, failures = jsx_gate.generate_with_gate(
            name,
            lambda error: _screen_source(name, page, siblings)
            if not error
            else _screen_source(name, page, siblings) + f"\n// retry after: {error}\n",
            skill_text,
        )
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


def apply_instruction(name: str, source: str, instruction: str, skill_text: str) -> str:
    updated = source
    if "</h1>" in source:
        updated = source.replace("</h1>", f"</h1>\n      <p>{instruction}</p>", 1)
    else:
        updated = source + f"\n// {instruction}\n"
    jsx_gate.compile_jsx(name, updated)
    failures = jsx_gate.conform(updated, skill_text)
    if failures:
        raise jsx_gate.CompileFailed(failures[0])
    return updated
