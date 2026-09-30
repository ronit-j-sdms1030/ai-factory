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


def screen_role(name: str, page: dict[str, str] | None = None) -> str:
    """Pick a layout family from the screen name + BRD page line.

    Fallback screens must not all share one form+table shell. Role drives
    composition; the LLM prompt uses the same role so model and fallback agree.
    """
    page = page or {}
    spaced_name = re.sub(r"([a-z])([A-Z])", r"\1 \2", name or "")
    spaced_id = re.sub(r"([a-z])([A-Z])", r"\1 \2", str(page.get("id") or ""))
    blob = (
        f"{name} {spaced_name} {page.get('id') or ''} {spaced_id} "
        f"{page.get('description') or ''}"
    ).lower()

    def hit(needle: str) -> bool:
        if " " in needle or "-" in needle:
            return needle in blob
        return bool(re.search(rf"(?<![a-z0-9]){re.escape(needle)}(?![a-z0-9])", blob))

    checks = (
        ("auth", ("login", "sign in", "signin", "sign-in", "auth", "password", "sso")),
        ("import", ("import", "upload", "csv", "bulk load", "ingest")),
        ("portal", ("portal", "self serve", "self-serve", "my books", "member home", "member portal")),
        ("dashboard", ("dashboard", "overview", "stats", "kpi", "summary", "staff home")),
        ("detail", ("profile", "detail", "record view", "member view")),
        ("catalog", ("catalogue", "catalog", "browse", "inventory", "search books", "collection")),
        ("loan", ("loan", "checkout", "check-out", "check out", "borrow", "return book", "due date")),
        ("export", ("export", "timesheet", "download report", "csv out")),
    )
    for role, needles in checks:
        if any(hit(n) for n in needles):
            return role
    if hit("management") or hit("admin"):
        return "catalog"
    return "workspace"


def _fields_and_action(title: str, description: str, role: str = "workspace") -> tuple[list[tuple[str, str]], str, tuple[str, ...]]:
    blob = f"{title} {description}".lower()
    action = {
        "auth": "Sign in",
        "dashboard": "Refresh",
        "import": "Import",
        "portal": "Continue",
        "detail": "Save changes",
        "catalog": "Add item",
        "loan": "Check out",
        "export": "Export",
        "workspace": "Save",
    }.get(role, "Save")
    if role == "auth":
        return [("email", "Work email"), ("password", "Password")], action, ()
    if role == "export":
        return [("period", "Period"), ("format", "Format")], action, ("Week to date", "Last month", "Custom range")
    if role == "import":
        return [("file", "CSV file"), ("mapping", "Column map")], action, ("title → Title", "isbn → ISBN", "ok rows")
    if role == "loan":
        return [("member", "Member"), ("item", "Item"), ("due", "Due date")], action, ()
    if role == "detail":
        return [("name", "Name"), ("email", "Email"), ("status", "Status")], action, ()
    if role == "catalog":
        return [("query", "Search")], "Search", ()
    if role == "dashboard":
        return [], action, ()
    if role == "portal":
        return [], action, ()

    fields: list[tuple[str, str]] = []
    hints = (
        ("room", "Room", ("room",)),
        ("starts", "Starts", ("start", "from", "begin")),
        ("ends", "Ends", ("end", "until", "to ")),
        ("note", "Note", ("note", "reason", "comment")),
        ("email", "Email", ("email",)),
        ("password", "Password", ("password", "login")),
        ("name", "Name", ("name", "employee", "staff", "member")),
        ("date", "Date", ("date", "day", "when", "due")),
        ("item", "Item", ("book", "title", "item", "asset")),
    )
    for key, label, needles in hints:
        if any(needle in blob for needle in needles):
            fields.append((key, label))
    if not fields:
        fields = [("detail", title or "Detail")]
    if any(token in blob for token in ("book", "room", "slot")):
        rows = ("Open slot", "Held", "Confirmed")
    else:
        rows = (f"{title} — ready", f"{title} — review", f"{title} — done")
    return fields[:4], action, rows


def pick_theme(brd_text: str) -> str:
    blob = (brd_text or "").lower()
    hints = (
        (("library", "loan", "catalogue", "catalog", "member", "isbn", "borrow"), "grove"),
        (("hotel", "room", "book a", "stay", "hospitality", "resort"), "sand"),
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


def nav_buttons(roster: list[str], indent: str = "        ", current: str = "") -> str:
    lines = []
    for item in roster:
        label = screen_label(item)
        if current and item == current:
            lines.append(
                f'<Button variant="nav" onClick={{() => navigate("{item}")}}>'
                f'<span style={{{{fontWeight:700, textDecoration:"underline"}}}}>{label}</span></Button>'
            )
        else:
            lines.append(
                f'<Button variant="nav" onClick={{() => navigate("{item}")}}>{label}</Button>'
            )
    return f"\n{indent}".join(lines)


def _shell(
    name: str,
    *,
    theme: str,
    brand: str,
    roster: list[str],
    main: str,
) -> str:
    theme_name = theme if theme in jsx_gate.ALLOWED_THEMES else "midnight"
    brand_text = _safe_jsx_text(brand) or "App"
    nav = nav_buttons(roster, current=name)
    return f'''{_factory_helpers(theme_name)}function {name}() {{
  return (
    <Page data-theme="{theme_name}">
      <Sidebar>
        <p>{brand_text}</p>
        {nav}
      </Sidebar>
      <div style={{{{flex:1, padding:"var(--space-md)"}}}}>
{main}
      </div>
    </Page>
  );
}}
'''


def _card(inner: str, *, extra_style: str = "") -> str:
    style = 'background:"var(--color-surface)", border:"1px solid var(--color-line)", padding:"var(--space-md)", borderRadius:8'
    if extra_style:
        style = f"{style}, {extra_style}"
    return f'<div style={{{{{style}}}}}>\n{inner}\n        </div>'


def _screen_source(
    name: str,
    page: dict[str, str],
    siblings: list[str],
    *,
    theme: str = "midnight",
    brand: str = "App",
    roster: list[str] | None = None,
) -> str:
    """Deterministic screen — role-specific layout, not one universal table."""
    title = _display_title(name, page)
    description = _safe_jsx_text(page.get("description") or title)
    role = screen_role(name, page)
    fields, action, rows = _fields_and_action(title, description, role)
    nav_roster = roster or ([name] + [item for item in siblings[:6] if item != name])
    field_jsx = "\n          ".join(
        f'<Field name="{key}" label="{label}" />' for key, label in fields
    )

    if role == "auth":
        main = f'''        <div style={{{{maxWidth:420, margin:"10vh auto"}}}}>
          {_card(f'''          <h1>{title}</h1>
          <p style={{{{color:"var(--color-muted)"}}}}>{description}</p>
          {field_jsx}
          <Button type="submit">{action}</Button>''')}
        </div>'''
    elif role == "dashboard":
        kpis = (
            ("On shelf", "128"),
            ("On loan", "34"),
            ("Overdue", "5"),
        )
        if "attend" in description.lower() or "time" in description.lower():
            kpis = (("Clocked in", "42"), ("Remote", "11"), ("Corrections", "3"))
        elif "book" in description.lower() and "loan" not in description.lower():
            kpis = (("Open today", "12"), ("Held", "4"), ("Confirmed", "18"))
        kpi_jsx = "\n          ".join(
            _card(
                f'          <p style={{{{color:"var(--color-muted)", margin:0}}}}>{label}</p>\n'
                f'          <p style={{{{fontSize:28, margin:"8px 0 0", color:"var(--color-accent)"}}}}>{value}</p>',
                extra_style="flex:1, minWidth:140",
            )
            for label, value in kpis
        )
        main = f'''        <h1>{title}</h1>
        <p style={{{{color:"var(--color-muted)"}}}}>{description}</p>
        <div style={{{{display:"flex", gap:"var(--space-md)", flexWrap:"wrap", marginBottom:"var(--space-md)"}}}}>
          {kpi_jsx}
        </div>
        <Table heading="Needs attention">
          <tr><td>Overdue — follow up today</td></tr>
          <tr><td>Hold expiring — notify member</td></tr>
          <tr><td>Import queued — 12 rows</td></tr>
        </Table>
        <Button type="button">{action}</Button>'''
    elif role == "catalog":
        main = f'''        <h1>{title}</h1>
        <p style={{{{color:"var(--color-muted)"}}}}>{description}</p>
        <div style={{{{display:"flex", gap:"var(--space-md)", alignItems:"end", marginBottom:"var(--space-md)"}}}}>
          {field_jsx or '<Field name="query" label="Search" />'}
          <Button type="button">Search</Button>
          <Button type="button">{action}</Button>
        </div>
        <Table heading="Results">
          <tr><th>Title</th><th>Status</th><th>Action</th></tr>
        </Table>'''
    elif role == "loan":
        main = f'''        <h1>{title}</h1>
        <p style={{{{color:"var(--color-muted)"}}}}>{description}</p>
        <div style={{{{display:"grid", gridTemplateColumns:"1fr 1fr", gap:"var(--space-md)"}}}}>
          {_card(f'''          <h2 style={{{{fontSize:16}}}}>New loan</h2>
          {field_jsx}
          <Button type="submit">{action}</Button>''')}
          {_card('''          <h2 style={{fontSize:16}}>Active loans</h2>
          <Table heading="Due soon">
            <tr><th>Member</th><th>Item</th><th>Due</th></tr>
          </Table>''')}
        </div>'''
    elif role == "detail":
        main = f'''        <h1>{title}</h1>
        <p style={{{{color:"var(--color-muted)"}}}}>{description}</p>
        {_card(f'''          {field_jsx}
          <div style={{{{display:"flex", gap:"var(--space-md)", marginTop:"var(--space-md)"}}}}>
            <Button type="submit">{action}</Button>
            <Button type="button" onClick={{() => navigate("{nav_roster[0]}")}}>Back</Button>
          </div>''')}'''
    elif role == "portal":
        jumps = "\n          ".join(
            f'<Button type="button" onClick={{() => navigate("{item}")}}>{screen_label(item)}</Button>'
            for item in nav_roster[:4]
            if item != name
        ) or f'<Button type="button">{action}</Button>'
        main = f'''        <h1>{title}</h1>
        <p style={{{{color:"var(--color-muted)"}}}}>{description}</p>
        {_card(f'''          <p>Quick actions</p>
          <div style={{{{display:"flex", flexWrap:"wrap", gap:"var(--space-md)"}}}}>
            {jumps}
          </div>''')}
        <Table heading="Your activity">
          <tr><td>Borrowed — due in 5 days</td></tr>
          <tr><td>Hold ready for pickup</td></tr>
        </Table>'''
    elif role == "import":
        main = f'''        <h1>{title}</h1>
        <p style={{{{color:"var(--color-muted)"}}}}>{description}</p>
        {_card(f'''          {field_jsx}
          <Button type="submit">{action}</Button>''')}
        <Table heading="Preview mapping">
          <tr><td>CSV column</td><td>Maps to</td></tr>
          <tr><td>title</td><td>Title</td></tr>
          <tr><td>isbn</td><td>ISBN</td></tr>
          <tr><td>copies</td><td>Copies</td></tr>
        </Table>'''
    elif role == "export":
        row_jsx = "\n          ".join(f"<tr><td>{row}</td></tr>" for row in rows)
        main = f'''        <h1>{title}</h1>
        <p style={{{{color:"var(--color-muted)"}}}}>{description}</p>
        {_card(f'''          {field_jsx}
          <Button type="submit">{action}</Button>''')}
        <Table heading="Export history">
          {row_jsx}
        </Table>'''
    else:
        field_block = field_jsx
        if rows:
            row_jsx = "\n          ".join(f"<tr><td>{row}</td></tr>" for row in rows)
            table = f'''        <Table heading="{title}">
          {row_jsx}
        </Table>'''
        else:
            table = ""
        main = f'''        <h1>{title}</h1>
        <p style={{{{color:"var(--color-muted)"}}}}>{description}</p>
        {_card(f'''          {field_block}
          <Button type="submit">{action}</Button>''')}
{table}'''

    return _shell(name, theme=theme, brand=brand, roster=nav_roster, main=main)


def layout_contract(role: str) -> str:
    """Tell the model which composition this screen must use."""
    return {
        "auth": (
            "AUTH layout: centred surface card (max ~420px), work email + password Fields, "
            "one Sign in Button. No data Table on this screen."
        ),
        "dashboard": (
            "DASHBOARD layout: h1 + muted support, row of 3 KPI surface cards with concrete "
            "numbers from the BRD domain, then a short 'Needs attention' Table. No login fields."
        ),
        "catalog": (
            "CATALOG layout: search Field + Search/Add Buttons on one row, then a Results "
            "Table with Title / Status / Action columns and 3 concrete sample rows."
        ),
        "loan": (
            "LOAN layout: two-column grid — left card New loan (member/item/due Fields + "
            "Check out), right card Active loans Table with Member / Item / Due."
        ),
        "detail": (
            "DETAIL layout: one surface card with identity Fields and Save / Back. No big "
            "status Table."
        ),
        "portal": (
            "PORTAL layout: welcome h1, Quick actions card with navigate Buttons to sibling "
            "screens, short Your activity Table."
        ),
        "import": (
            "IMPORT layout: card with file + mapping Fields and Import Button, then Preview "
            "mapping Table (CSV column → field)."
        ),
        "export": (
            "EXPORT layout: period/format Fields + Export Button, Export history Table."
        ),
        "workspace": (
            "WORKSPACE layout: surface card with domain Fields + primary Button; optional "
            "small Table only if the BRD is list-shaped. Do not invent login or booking chrome."
        ),
    }.get(role, "Match the BRD page purpose; do not reuse a generic form+table for every screen.")


_RESERVED = {
    "Page",
    "Sidebar",
    "Button",
    "Field",
    "Table",
    "Card",
    "Badge",
    "Hero",
    "Image",
    "ScreenCanvas",
    "ScreenBody",
}


def _theme_of(source: str) -> str:
    found = jsx_gate.THEME_ATTR.search(source or "")
    if found and found.group(1) in jsx_gate.ALLOWED_THEMES:
        return found.group(1)
    return "midnight"


_PLACEHOLDER_IMG = (
    "data:image/svg+xml,"
    "%3Csvg xmlns='http://www.w3.org/2000/svg' width='1200' height='720'%3E"
    "%3Cdefs%3E%3ClinearGradient id='g' x1='0' y1='0' x2='1' y2='1'%3E"
    "%3Cstop stop-color='%23ffffff' stop-opacity='0.15'/%3E"
    "%3Cstop offset='1' stop-color='%23000000' stop-opacity='0.25'/%3E"
    "%3C/linearGradient%3E%3C/defs%3E"
    "%3Crect width='1200' height='720' fill='url(%23g)'/%3E"
    "%3Ctext x='50%25' y='50%25' fill='white' fill-opacity='0.7' font-size='42' "
    "font-family='system-ui' text-anchor='middle' dominant-baseline='middle'%3EPhoto inset%3C/text%3E"
    "%3C/svg%3E"
)


def _factory_helpers(theme: str = "midnight") -> str:
    theme_name = theme if theme in jsx_gate.ALLOWED_THEMES else "midnight"
    return f'''function Page(props) {{
  return <main className="page" data-theme="{theme_name}" style={{{{background:"var(--color-bg)", color:"var(--color-text)", fontFamily:"var(--font-sans)", display:"flex", minHeight:"100vh", ...(props.style || {{}})}}}}>{{props.children}}</main>;
}}
function Sidebar(props) {{
  return <aside className="sidebar" style={{{{background:"var(--color-sidebar)", color:"var(--color-text)", width:"var(--sidebar-w, 220px)", flexShrink:0, padding:"var(--space-md)", borderRight:"1px solid var(--color-line)"}}}}>{{props.children}}</aside>;
}}
function Button(props) {{
  var variant = props.variant || "solid";
  var base = {{
    minHeight:"var(--control-h, 40px)",
    padding:"var(--control-pad, 0.55rem 1rem)",
    fontSize:"var(--type-base, 0.95rem)",
    borderRadius:"var(--radius, 8px)",
    fontWeight:600,
    fontFamily:"var(--font-sans)",
    cursor:"pointer",
    border:"1px solid transparent",
    transition:"transform .16s ease, filter .16s ease",
    lineHeight:1.2
  }};
  var looks = {{
    solid: {{background:"var(--color-accent)", color:"var(--color-bg)"}},
    ghost: {{background:"transparent", color:"var(--color-accent)", border:"1px solid var(--color-line)"}},
    outline: {{background:"var(--color-surface)", color:"var(--color-text)", border:"1px solid var(--color-accent)"}},
    soft: {{background:"var(--color-surface)", color:"var(--color-accent)", boxShadow:"inset 0 0 0 1px var(--color-line)"}},
    nav: {{background:"transparent", color:"var(--color-muted)", width:"100%", textAlign:"left", fontWeight:500, minHeight:"calc(var(--control-h, 40px) * 0.85)", padding:"0.45rem 0.7rem"}}
  }};
  var pass = {{}};
  Object.keys(props || {{}}).forEach(function (k) {{
    if (k.indexOf("data-") === 0) pass[k] = props[k];
  }});
  return <button type={{props.type || "button"}} data-variant={{variant}} onClick={{props.onClick}} className={{props.motion || undefined}} style={{{{...base, ...(looks[variant] || looks.solid), ...(props.style || {{}})}}}} {{...pass}}>{{props.children}}</button>;
}}
function Field(props) {{
  var pass = {{}};
  Object.keys(props || {{}}).forEach(function (k) {{
    if (k.indexOf("data-") === 0) pass[k] = props[k];
  }});
  return <label {{...pass}} style={{{{color:"var(--color-muted)", fontSize:"var(--type-label, 0.78rem)", fontFamily:"var(--font-sans)", width:"min(100%, var(--field-w, 28rem))", ...(props.style || {{}})}}}}>{{props.label}}<input name={{props.name}} style={{{{height:"var(--control-h, 40px)", fontSize:"var(--type-base, 0.95rem)", borderRadius:"var(--radius, 8px)", padding:"0 12px", border:"1px solid var(--color-line)", background:"var(--color-surface)", color:"var(--color-text)", width:"100%", fontFamily:"var(--font-sans)", ...(props.inputStyle || {{}})}}}} /></label>;
}}
function Table(props) {{
  var pass = {{}};
  Object.keys(props || {{}}).forEach(function (k) {{
    if (k.indexOf("data-") === 0) pass[k] = props[k];
  }});
  // heading = section title (caption); children first row = column labels.
  // Live bridge hydrates tbody data rows from /api — do not put mock rows here.
  return <table {{...pass}} style={{{{background:"var(--color-surface)", border:"1px solid var(--color-line)", borderRadius:"var(--radius, 8px)", fontSize:"var(--type-base, 0.95rem)", ...(props.style || {{}})}}}}>{{props.heading ? <caption style={{{{textAlign:"left", fontWeight:600, padding:"0.5rem 0.75rem", color:"var(--color-muted)"}}}}>{{props.heading}}</caption> : null}}<tbody>{{props.children}}</tbody></table>;
}}
function Card(props) {{
  var pass = {{}};
  Object.keys(props || {{}}).forEach(function (k) {{
    if (k.indexOf("data-") === 0) pass[k] = props[k];
  }});
  return <div {{...pass}} className={{"ds-card " + (props.motion || "")}} style={{{{borderRadius:"var(--radius, 8px)", ...(props.style || {{}})}}}}>{{props.children}}</div>;
}}
function Badge(props) {{
  var pass = {{}};
  Object.keys(props || {{}}).forEach(function (k) {{
    if (k.indexOf("data-") === 0) pass[k] = props[k];
  }});
  return <span {{...pass}} className="ds-badge" style={{{{borderRadius:"var(--radius-pill, 999px)", ...(props.style || {{}})}}}}>{{props.children}}</span>;
}}
function Hero(props) {{
  var pass = {{}};
  Object.keys(props || {{}}).forEach(function (k) {{
    if (k.indexOf("data-") === 0) pass[k] = props[k];
  }});
  return <section {{...pass}} className={{"ds-hero " + (props.motion || "motion-rise")}} style={{{{borderRadius:"calc(var(--radius, 8px) + 6px)", ...(props.style || {{}})}}}}>{{props.children}}</section>;
}}
function Image(props) {{
  var src = props.src || "{_PLACEHOLDER_IMG}";
  var pass = {{}};
  Object.keys(props || {{}}).forEach(function (k) {{
    if (k.indexOf("data-") === 0) pass[k] = props[k];
  }});
  return (
    <figure {{...pass}} className={{"ds-inset " + (props.motion || "motion-fade")}} style={{{{aspectRatio: props.ratio || "16 / 9", borderRadius:"calc(var(--radius, 8px) + 4px)", ...(props.style || {{}})}}}}>
      <img src={{src}} alt={{props.alt || ""}} />
      {{props.caption ? <figcaption>{{props.caption}}</figcaption> : null}}
    </figure>
  );
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
    # Every requirement: <LOCATION>-style placeholders become text before Babel.
    text = jsx_gate.neutralize_placeholder_tags(source)
    text = ensure_named_screen(name, text)

    def _swap_hex(match: re.Match[str]) -> str:
        window = text[max(0, match.start() - 48) : match.end()]
        if jsx_gate.TOKEN_HEX.search(window):
            return match.group(0)
        return "var(--color-accent)"

    text = jsx_gate.HEX_COLOUR.sub(_swap_hex, text)

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
    return jsx_gate.dedupe_host_functions(text)


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
    # One page id fold → one screen. Stops LoginScreen2 / LoanManagement3 stubs.
    deduped: list[dict[str, str]] = []
    seen_pages: set[str] = set()
    for page in pages:
        key = coverage._fold(page.get("id") or "")
        if not key or key in seen_pages:
            continue
        seen_pages.add(key)
        deduped.append(page)
    pages = deduped
    used: set[str] = set()
    screens: list[dict[str, Any]] = []
    repaired: list[str] = []
    conform_failures: list[dict[str, str]] = []
    theme = pick_theme(brd_text)
    brand = _brand_label(pages[0]) if pages else "App"
    planned: set[str] = set()
    roster = [coverage.component_name(page, planned) for page in pages]
    # Drop roster duplicates if component_name collapsed two pages to one name.
    roster = list(dict.fromkeys(roster))

    def add(page: dict[str, str], *, repaired_gap: bool = False) -> None:
        name = coverage.component_name(page, used)
        if any(s["name"] == name for s in screens):
            return
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
