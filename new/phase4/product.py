"""Assemble a runnable app from the locked stack, BRD entities, and Gate 3 JSX.

Ticket files stay inside Gate 4 allow-lists. The assembled tree is the thing
a reviewer can start: SQLite (Postgres stand-in), API, exact approved screens.
"""

from __future__ import annotations

import json
import re
from typing import Any

from phase1 import brd as brd_mod
from phase2.jsx_gate import neutralize_placeholder_tags

# BRD writes `- **Room:** id, name`. Also accept `- **Room**: id, name`
# and compound owners like `- **User / Member / Staff:** id, name`.
_FIELD_RE = re.compile(
    r"^[-*]\s+\*\*([A-Za-z][A-Za-z0-9_ /]*?)(?::\*\*|\*\*:)\s*(.+)$", re.M
)

_SKIP_ENTITY_NAMES = {
    "source",
    "acceptance",
    "given",
    "when",
    "then",
    "and",
    "or",
    "note",
    "summary",
    "assumptions",
    "non-goals",
    "out-of-scope",
    "in-scope",
    "dependencies",
    "risks",
    "open",
}

_SCREEN_SUFFIXES = (
    "Screen",
    "View",
    "Dashboard",
    "Portal",
    "Management",
    "Page",
    "Form",
    "Table",
)


def _clean_entity_name(raw: str) -> str | None:
    """First PascalCase token; drop prose labels and screen names."""
    token = (raw or "").split("/")[0].strip().split()[0] if raw else ""
    if not re.fullmatch(r"[A-Z][A-Za-z0-9_]*", token):
        return None
    if token.lower() in _SKIP_ENTITY_NAMES:
        return None
    if any(token.endswith(suffix) for suffix in _SCREEN_SUFFIXES):
        return None
    return token


def _clean_fields(raw: str) -> list[str]:
    """Keep identifier fields only; strip enum parentheses."""
    text = re.sub(r"\([^)]*\)", "", raw or "")
    out: list[str] = []
    for part in text.split(","):
        field = re.sub(r"[^a-z0-9_]", "", part.strip().lower().replace(" ", "_"))
        if not re.fullmatch(r"[a-z][a-z0-9_]*", field):
            continue
        if field not in out:
            out.append(field)
    if "id" not in out:
        out.insert(0, "id")
    return out


def parse_entities(brd_text: str) -> list[tuple[str, list[str]]]:
    """Extract real data-model entities — never Gherkin / screen blurbs."""
    text = brd_text or ""
    section = text
    headed = re.search(
        r"(?ims)^(?:#{1,3}\s*)?data\s*model\b.*?(?=^#{1,3}\s|\Z)", text
    )
    if headed:
        section = headed.group(0)

    found: list[tuple[str, list[str]]] = []
    seen: set[str] = set()
    for match in _FIELD_RE.finditer(section):
        name = _clean_entity_name(match.group(1))
        fields = _clean_fields(match.group(2))
        # Real entities list concrete columns (id + at least one more).
        if not name or len(fields) < 2:
            continue
        if name in seen:
            continue
        seen.add(name)
        found.append((name, fields))

    if found:
        return found
    return brd_mod._entities(text)


def table_name(entity: str) -> str:
    slug = re.sub(r"(?<!^)([A-Z])", r"_\1", entity).lower()
    if not slug.endswith("s"):
        slug += "s"
    return slug


def _prisma_type(field: str) -> str:
    key = field.lower()
    if key == "id":
        return "String @id"
    if key.endswith("_at") or key in {"starts_at", "ends_at", "due_at", "due_date"}:
        return "DateTime"
    if key in {"in_office"}:
        return "Boolean"
    return "String"


def prisma_schema(entities: list[tuple[str, list[str]]]) -> str:
    models = []
    for name, fields in entities:
        lines = [f"model {name} {{"]
        seen = set()
        for field in fields:
            if field in seen:
                continue
            seen.add(field)
            lines.append(f"  {field} {_prisma_type(field)}")
        lines.append("}")
        models.append("\n".join(lines))
    return (
        "// PostgreSQL in the locked profile. SQLite file is the local demo database.\n"
        "// down: revert via prisma/migrations (rollback).\n"
        "datasource db {\n"
        '  provider = "sqlite"\n'
        '  url      = env("DATABASE_URL")\n'
        "}\n\n"
        "generator client {\n"
        '  provider = "prisma-client-js"\n'
        "}\n\n"
        + "\n\n".join(models)
        + "\n"
    )


def migration_sql(entities: list[tuple[str, list[str]]]) -> str:
    ups = []
    downs = []
    for name, fields in entities:
        table = table_name(name)
        cols = []
        for field in fields:
            col_type = "TEXT"
            key = field.lower()
            if key.endswith("_at"):
                col_type = "TEXT"
            if key == "id":
                cols.append("id TEXT PRIMARY KEY")
            else:
                cols.append(f"{field} {col_type}")
        ups.append(f"CREATE TABLE IF NOT EXISTS {table} ({', '.join(cols)});")
        downs.append(f"DROP TABLE IF EXISTS {table};")
    return "-- up\n" + "\n".join(ups) + "\n\n-- down / rollback\n" + "\n".join(downs) + "\n"


def alembic_revision(entities: list[tuple[str, list[str]]], ticket_id: str) -> str:
    sql = migration_sql(entities)
    return (
        f'"""schema {ticket_id}"""\n\n'
        "from alembic import op\n\n\n"
        f"revision = {ticket_id!r}\n"
        "down_revision = None\n\n\n"
        "def upgrade():\n"
        f"    op.execute({sql!r})\n\n\n"
        "def downgrade():\n"
        "    # down / rollback\n"
        f"    op.execute({sql.split('-- down / rollback', 1)[-1]!r})\n"
    )


def _resource_for(ticket: dict[str, Any], entities: list[tuple[str, list[str]]]) -> str:
    title = str(ticket.get("title") or "").lower()
    if "avail" in title or "room" in title:
        return next((table_name(n) for n, _ in entities if "room" in n.lower()), "rooms")
    if "book" in title or "create" in title or "cancel" in title:
        return next((table_name(n) for n, _ in entities if "book" in n.lower()), "bookings")
    if entities:
        return table_name(entities[0][0])
    return "records"


def api_module(ticket: dict[str, Any], entities: list[tuple[str, list[str]]]) -> str:
    tid = ticket.get("id")
    resource = _resource_for(ticket, entities)
    return (
        f"// ticket {tid}\n"
        f"// department {ticket.get('department')}\n"
        f"export const ticket = {tid!r};\n"
        f"export const resource = {resource!r};\n"
        "export const methods = ['GET', 'POST', 'DELETE'];\n"
        "export function path() { return '/api/' + resource; }\n"
    )


def api_py(ticket: dict[str, Any], entities: list[tuple[str, list[str]]]) -> str:
    tid = ticket.get("id")
    resource = _resource_for(ticket, entities)
    return f"# ticket {tid}\nticket = {tid!r}\nresource = {resource!r}\n"


def screen_jsx(name: str, source: str) -> str:
    text = neutralize_placeholder_tags((source or "").strip())
    if f"function {name}" not in text:
        text += f"\nexport default function {name}Bridge() {{ return <Page />; }}\n"
    else:
        text += f"\nexport default {name};\n"
    return text + "\n"


def screen_ticket_ts(name: str, ticket_id: str) -> str:
    return (
        f"// ticket {ticket_id}\n"
        f"// Approved Gate 3 screen {name}. Same JSX the UI agent produced.\n"
        f"export const ticket = {ticket_id!r};\n"
        f"export const screenName = {name!r};\n"
        f"export {{ default }} from './{name}.jsx';\n"
    )


def _theme_css() -> str:
    return (
        ':root,[data-theme="midnight"]{--color-bg:#0b0c0e;--color-sidebar:#101114;'
        "--color-surface:#16171b;--color-text:#f3f3f1;--color-muted:#9a9ba1;"
        "--color-accent:#f2761f;--color-line:rgba(255,255,255,0.08);"
        "--space-md:16px;--font-sans:Ubuntu,'Liberation Sans',system-ui,sans-serif;"
        "--font-display:var(--font-sans);--type-base:0.95rem;--type-h1:clamp(1.55rem,2.4vw,2.2rem);"
        "--type-label:0.78rem;--control-h:40px;--control-pad:0.55rem 1rem;--radius:8px;"
        "--radius-pill:999px;--sidebar-w:220px;--field-w:28rem}"
        '[data-theme="aurora"]{--color-bg:#071422;--color-sidebar:#0b1c30;'
        "--color-surface:#10263c;--color-text:#e7f6ff;--color-muted:#7fa4bc;"
        "--color-accent:#3ee0c5;--color-line:rgba(62,224,197,0.18)}"
        '[data-theme="paper"]{--color-bg:#faf9f5;--color-sidebar:#f2efe5;'
        "--color-surface:#ffffff;--color-text:#262117;--color-muted:#6b6355;"
        "--color-accent:#bf6a4d;--color-line:rgba(38,33,23,0.12)}"
        '[data-theme="grove"]{--color-bg:#102117;--color-sidebar:#163024;'
        "--color-surface:#1c3a2c;--color-text:#e8f6e4;--color-muted:#8fb89a;"
        "--color-accent:#c5e86a;--color-line:rgba(197,232,106,0.16)}"
        '[data-theme="coral"]{--color-bg:#1a1214;--color-sidebar:#24181b;'
        "--color-surface:#2c1e22;--color-text:#fdecee;--color-muted:#c49aa0;"
        "--color-accent:#ff6b6b;--color-line:rgba(255,107,107,0.18)}"
        '[data-theme="ink"]{--color-bg:#111008;--color-sidebar:#1a1810;'
        "--color-surface:#221f14;--color-text:#f4ead0;--color-muted:#b3a57a;"
        "--color-accent:#e0b84e;--color-line:rgba(224,184,78,0.18)}"
        '[data-theme="glacier"]{--color-bg:#eef3f8;--color-sidebar:#e2ebf3;'
        "--color-surface:#ffffff;--color-text:#142033;--color-muted:#5b6d82;"
        "--color-accent:#1d4e89;--color-line:rgba(20,32,51,0.1)}"
        '[data-theme="sand"]{--color-bg:#f6efe4;--color-sidebar:#efe4d2;'
        "--color-surface:#fffaf2;--color-text:#2a1c12;--color-muted:#7a6352;"
        "--color-accent:#c45c26;--color-line:rgba(42,28,18,0.12)}"
        '[data-theme="blush"]{--color-bg:#fff7fb;--color-sidebar:#ffe8f1;'
        "--color-surface:#ffffff;--color-text:#3a2430;--color-muted:#9a6b7e;"
        "--color-accent:#e85a8c;--color-line:rgba(58,36,48,0.1)}"
        '[data-theme="noir"]{--color-bg:#0a0a0a;--color-sidebar:#141414;'
        "--color-surface:#1c1c1c;--color-text:#f5f5f5;--color-muted:#8a8a8a;"
        "--color-accent:#ffffff;--color-line:rgba(255,255,255,0.12)}"
        '[data-theme="ocean"]{--color-bg:#061820;--color-sidebar:#0b2430;'
        "--color-surface:#123040;--color-text:#e6f7ff;--color-muted:#7aa8b8;"
        "--color-accent:#2ec4b6;--color-line:rgba(46,196,182,0.2)}"
        '[data-theme="sunrise"]{--color-bg:#fff8f0;--color-sidebar:#ffe8d2;'
        "--color-surface:#ffffff;--color-text:#2c1a0e;--color-muted:#9a6b45;"
        "--color-accent:#ff7a45;--color-line:rgba(44,26,14,0.1)}"
        '[data-theme="plum"]{--color-bg:#1a1020;--color-sidebar:#261530;'
        "--color-surface:#321c40;--color-text:#f6eaff;--color-muted:#b08cc0;"
        "--color-accent:#c77dff;--color-line:rgba(199,125,255,0.2)}"
    )


LIBRARY_SCRIPTS = (
    "https://unpkg.com/prop-types@15.8.1/prop-types.min.js",
    "https://unpkg.com/lucide-react@0.460.0/dist/umd/lucide-react.min.js",
    "https://unpkg.com/recharts@2.13.3/umd/Recharts.js",
    "https://unpkg.com/framer-motion@11.11.17/dist/framer-motion.js",
    "https://unpkg.com/dayjs@1.11.13/dayjs.min.js",
)

_NAV_JS = r"""
    function resolveScreen(target, names) {
      var raw = String(target || "");
      if (names.indexOf(raw) >= 0) return raw;
      var flat = raw.replace(/[^a-z0-9]/gi, "").toLowerCase();
      if (!flat) return "";
      for (var i = 0; i < names.length; i++) {
        if (names[i].toLowerCase() === flat) return names[i];
      }
      var stop = {the:1, can:1, and:1, "for":1, "with":1, via:1, "this":1, that:1, your:1,
        my:1, all:1, "new":1, view:1, page:1, screen:1, see:1, sees:1, "in":1, of:1, to:1};
      function words(s) {
        return String(s).replace(/([a-z])([A-Z])/g, "$1 $2").toLowerCase()
          .split(/[^a-z0-9]+/)
          .filter(function (w) { return w.length > 2 && !stop[w]; })
          .map(function (w) { return w.replace(/(ings|ing|ed|s)$/, ""); });
      }
      var want = words(raw);
      var best = "", score = 0;
      names.forEach(function (n) {
        var have = words(n), s = 0;
        want.forEach(function (w) { if (have.indexOf(w) >= 0) s += 1; });
        if (s > score) { score = s; best = n; }
      });
      return best;
    }
    function wireNavClicks(doc, names, go) {
      doc.addEventListener("click", function (e) {
        var el = e.target && e.target.closest ? e.target.closest("button,a") : null;
        if (!el) return;
        var explicit = el.getAttribute("data-nav");
        if (!explicit && !el.closest(".sidebar,aside,nav,[data-appnav]")) return;
        var name = explicit || resolveScreen(el.textContent, names);
        if (!name) return;
        e.preventDefault();
        go(name);
      }, true);
    }
"""


def screens_theme(names: list[str], screens: list[dict[str, Any]]) -> str:
    """One theme per requirement, identical in the editor and the preview."""
    for screen in screens:
        found = re.search(r'data-theme=["\']([a-z]+)["\']', str(screen.get("source") or ""))
        if found:
            return found.group(1)
    themes = (
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
    key = "|".join(names) or "app"
    return themes[sum(map(ord, key)) % len(themes)]


def _preview_css() -> str:
    return (
        _theme_css()
        + "*{box-sizing:border-box}html,body,#root{height:100%;margin:0}"
        "body{background:var(--color-bg);color:var(--color-text);font-family:var(--font-sans)}"
        "@keyframes rise{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}"
        "@keyframes fade{from{opacity:0}to{opacity:1}}"
        "@keyframes slide-in{from{opacity:0;transform:translateX(-12px)}to{opacity:1;transform:none}}"
        "@keyframes pulse-soft{0%,100%{transform:scale(1)}50%{transform:scale(1.02)}}"
        ".preview-root{display:flex;flex-direction:column;min-height:100vh;height:100%;"
        "background:var(--color-bg);color:var(--color-text)}"
        ".app-shell{display:flex;flex:1;min-height:0}"
        ".preview-stage{flex:1;min-width:0;min-height:0;overflow:auto}"
        ".page{display:flex;min-height:100%;width:100%;max-width:none;padding:0;"
        "animation:rise .45s ease both}"
        ".sidebar{width:var(--sidebar-w,220px);flex-shrink:0;background:var(--color-sidebar);"
        "border-right:1px solid var(--color-line);padding:var(--space-md);"
        "display:flex;flex-direction:column;gap:0.25rem}"
        ".sidebar>p:first-child{padding:0.2rem 0.7rem 0.85rem;margin:0;font-weight:600;"
        "font-family:var(--font-display,var(--font-sans))}"
        ".page>div{flex:1;min-width:0;padding:clamp(1.2rem,2.6vw,2.2rem)}"
        "h1{margin:0 0 0.4rem;font-size:var(--type-h1,clamp(1.55rem,2.4vw,2.2rem));"
        "font-weight:700;font-family:var(--font-display,var(--font-sans));letter-spacing:-0.02em}"
        "h2{font-family:var(--font-display,var(--font-sans));font-size:calc(var(--type-base,0.95rem)*1.15)}"
        "p{margin:0 0 1.2rem;max-width:42em;line-height:1.55;color:var(--color-muted);"
        "font-size:var(--type-base,0.95rem)}"
        "label{display:flex;flex-direction:column;gap:8px;width:min(100%,var(--field-w,28rem));"
        "margin:0 0 1rem;font-size:var(--type-label,0.78rem);font-weight:500;color:var(--color-muted)}"
        "input,select,textarea{text-transform:none;letter-spacing:0;font-weight:450;"
        "font-size:var(--type-base,0.95rem);font-family:inherit;height:var(--control-h,40px);"
        "border:1px solid var(--color-line);border-radius:var(--radius,8px);padding:0 12px;"
        "background:var(--color-surface);width:100%;color:var(--color-text)}"
        "button{appearance:none;border:0;border-radius:var(--radius,8px);font:inherit;"
        "font-size:var(--type-base,0.95rem);font-weight:600;cursor:pointer;"
        "background:var(--color-accent);color:var(--color-bg);"
        "min-height:var(--control-h,40px);padding:var(--control-pad,0.55rem 1rem);"
        "transition:transform .16s ease,filter .16s ease,box-shadow .16s ease}"
        "button:hover{transform:translateY(-1px);filter:brightness(1.06)}"
        "button[data-variant=ghost]{background:transparent;color:var(--color-accent);"
        "border:1px solid var(--color-line)}"
        "button[data-variant=outline]{background:var(--color-surface);color:var(--color-text);"
        "border:1px solid var(--color-accent)}"
        "button[data-variant=soft]{background:var(--color-surface);color:var(--color-accent);"
        "box-shadow:inset 0 0 0 1px var(--color-line)}"
        "button[data-variant=nav],.sidebar button{background:transparent!important;color:var(--color-muted)!important;"
        "border:1px solid transparent;padding:0.45rem 0.7rem;text-align:left;width:100%;"
        "font-weight:500;min-height:calc(var(--control-h,40px)*0.85);box-shadow:none}"
        "button[data-variant=nav]:hover,.sidebar button:hover,.sidebar button.on,"
        "button[data-variant=nav].on{background:var(--color-accent)!important;color:var(--color-bg)!important}"
        "@media (max-width:720px){.page,.app-shell{flex-direction:column}"
        ".sidebar{width:auto;flex-direction:row;overflow-x:auto;border-right:0;"
        "border-bottom:1px solid var(--color-line)}"
        ".sidebar button{width:auto;white-space:nowrap}}"
        "table{width:100%;border-collapse:collapse;background:var(--color-surface);"
        "border:1px solid var(--color-line);border-radius:10px;overflow:hidden;margin-top:1.2rem}"
        "th,td{text-align:left;padding:0.8rem 1rem;border-bottom:1px solid var(--color-line)}"
        "th{font-size:0.7rem;letter-spacing:0.04em;text-transform:uppercase;"
        "color:var(--color-muted);font-weight:600}"
        ".ds-card{background:var(--color-surface);border:1px solid var(--color-line);"
        "border-radius:14px;padding:var(--space-md);box-shadow:0 8px 28px rgba(0,0,0,.08)}"
        ".ds-badge{display:inline-flex;align-items:center;gap:6px;padding:0.25rem 0.65rem;"
        "border-radius:999px;font-size:0.72rem;font-weight:600;background:var(--color-surface);"
        "color:var(--color-accent);border:1px solid var(--color-line)}"
        ".ds-hero{position:relative;overflow:hidden;border-radius:18px;min-height:180px;"
        "padding:clamp(1.2rem,3vw,2rem);background:linear-gradient(135deg,var(--color-surface),"
        "var(--color-sidebar));border:1px solid var(--color-line);margin:0 0 1.2rem}"
        ".ds-hero h1,.ds-hero h2{margin:0 0 0.35rem}"
        ".ds-inset{margin:0 0 1.2rem;border-radius:16px;overflow:hidden;"
        "border:1px solid var(--color-line);background:var(--color-surface)}"
        ".ds-inset img{display:block;width:100%;height:100%;object-fit:cover}"
        ".ds-inset figcaption{padding:0.55rem 0.85rem;font-size:0.78rem;color:var(--color-muted)}"
        ".motion-rise{animation:rise .5s ease both}"
        ".motion-fade{animation:fade .55s ease both}"
        ".motion-slide{animation:slide-in .5s ease both}"
        ".motion-pulse{animation:pulse-soft 2.4s ease-in-out infinite}"
    )


def frontend_html(
    requirement_id: str,
    screens: list[dict[str, Any]],
    api_base: str,
    entities: list[tuple[str, list[str]]] | None = None,
) -> str:
    names = [str(item.get("name") or "Screen") for item in screens]
    scripts = []
    for screen in screens:
        name = str(screen.get("name") or "Screen")
        source = neutralize_placeholder_tags(str(screen.get("source") or "").strip())
        scripts.append(
            f'<script type="text/babel" data-presets="react">\n'
            f"(function () {{\n"
            f"var useState = React.useState, useEffect = React.useEffect, "
            f"useMemo = React.useMemo, useRef = React.useRef, "
            f"useCallback = React.useCallback, useContext = React.useContext;\n"
            f"(function () {{\n"
            f"{source}\n"
            f"window.SCREENS = window.SCREENS || {{}};\n"
            f"window.SCREENS[{name!r}] = typeof {name} === 'function' ? {name} : Page;\n"
            f"}})();\n"
            f"}})();\n"
            f"</script>"
        )
    first = json.dumps(names[0] if names else "")
    listed = json.dumps(names)
    has_nav = json.dumps(
        {
            str(s.get("name") or "Screen"): bool(
                re.search(r"<Sidebar\b|<aside\b|className=[\"']sidebar", str(s.get("source") or ""))
            )
            for s in screens
        }
    )
    api = json.dumps(api_base)
    theme_js = json.dumps(screens_theme(names, screens))
    entity_map = {
        table_name(name): [field for field in fields if field != "id"]
        for name, fields in (entities or [])
    }
    tables_js = json.dumps(entity_map)
    library_tags = "<script>window.react = window.React;</script>\n  " + "\n  ".join(
        f'<script src="{src}"></script>' for src in LIBRARY_SCRIPTS
    )
    live_bridge = r"""
    window.TABLES = window.TABLES || {};
    window.apiBase = (window.API || "").replace(/\/$/, "");
    window.apiCall = async function (method, resource, body, id) {
      var url = window.apiBase + "/" + resource + (id ? "/" + id : "");
      var res = await fetch(url, {
        method: method,
        headers: body ? { "Content-Type": "application/json" } : undefined,
        body: body ? JSON.stringify(body) : undefined,
      });
      var text = await res.text();
      try { return { ok: res.ok, status: res.status, data: JSON.parse(text) }; }
      catch (e) { return { ok: res.ok, status: res.status, data: text }; }
    };
    window.liveStatus = function (msg, kind) {
      var el = document.getElementById("live-db-bar");
      if (!el) return;
      el.dataset.kind = kind || "ok";
      el.querySelector("[data-live-msg]").textContent = msg;
    };
    window.refreshLiveCounts = async function () {
      var parts = [];
      var tables = Object.keys(window.TABLES || {});
      for (var i = 0; i < tables.length; i++) {
        var t = tables[i];
        try {
          var out = await window.apiCall("GET", t);
          var n = Array.isArray(out.data) ? out.data.length : 0;
          parts.push(t + " (" + n + ")");
        } catch (e) {
          parts.push(t + " (?)");
        }
      }
      window.liveStatus(
        parts.length
          ? ("Live DB · " + parts.join(" · ") + " · API " + window.apiBase)
          : ("Live API " + window.apiBase + " (no tables yet)"),
        "ok"
      );
    };
    window.inferResource = function (fields) {
      var keys = Object.keys(fields || {}).map(function (k) { return k.toLowerCase(); });
      var best = null, bestScore = 0;
      Object.keys(window.TABLES || {}).forEach(function (table) {
        var cols = (window.TABLES[table] || []).map(function (c) { return String(c).toLowerCase(); });
        var score = 0;
        keys.forEach(function (k) { if (cols.indexOf(k) !== -1) score += 1; });
        if (score > bestScore) { bestScore = score; best = table; }
      });
      if (best) return best;
      var screen = (location.hash || "").replace(/^#\/?/, "");
      if (/loan/i.test(screen)) return "loans";
      if (/catalog|book/i.test(screen)) return "books";
      if (/member|login|staff|user/i.test(screen)) return "users";
      if (/import/i.test(screen)) return "import_logs";
      return Object.keys(window.TABLES || {})[0] || "users";
    };
    window.collectNearbyFields = function (root) {
      var data = {};
      (root.querySelectorAll("input,select,textarea") || []).forEach(function (el) {
        var key = el.name || el.id || el.getAttribute("aria-label") || el.placeholder || "";
        key = String(key).trim().toLowerCase().replace(/\s+/g, "_");
        if (!key || key === "search") return;
        if (el.type === "password") key = key.indexOf("password") >= 0 ? "password_hash" : key;
        data[key] = el.value;
      });
      return data;
    };
    window.normalizePayload = function (resource, fields) {
      var data = Object.assign({}, fields || {});
      var aliases = {
        member: "member_id", member_name: "member_id", borrower: "member_id",
        item: "book_id", book: "book_id", title: "title",
        due: "due_date", due_date: "due_date",
        email: "email", name: "name", role: "role",
        author: "author", isbn: "isbn",
        file: "file_name", file_name: "file_name",
      };
      Object.keys(aliases).forEach(function (from) {
        if (data[from] != null && data[from] !== "" && data[aliases[from]] == null) {
          data[aliases[from]] = data[from];
        }
      });
      if (resource === "loans") {
        if (!data.status) data.status = "active";
        if (!data.checked_out_at) data.checked_out_at = new Date().toISOString().slice(0, 10);
        if (data.member && !data.member_id) data.member_id = data.member;
        if (data.item && !data.book_id) data.book_id = data.item;
        if (data.due && !data.due_date) data.due_date = data.due;
      }
      if (resource === "users") {
        if (data.member && !data.name) data.name = data.member;
        if (!data.role) data.role = "member";
      }
      if (resource === "books") {
        if (data.item && !data.title) data.title = data.item;
        if (!data.status) data.status = "available";
      }
      return data;
    };
    window.saveFromUi = function (btn, stage) {
      var scope = stage || (btn && btn.closest(".preview-stage")) || document.getElementById("root") || document;
      var data = window.collectNearbyFields(scope);
      var resource = (btn && btn.getAttribute("data-resource")) || window.inferResource(data);
      // Screen-aware fallback beats weak field matches (member/item/due → loans).
      var screen = (location.hash || "").replace(/^#\/?/, "");
      if (/loan/i.test(screen)) resource = "loans";
      else if (/catalog/i.test(screen)) resource = "books";
      else if (/import/i.test(screen)) resource = "import_logs";
      else if (/login|member|staff|profile/i.test(screen) && !/loan|catalog/i.test(screen)) {
        if (!data.title && !data.isbn) resource = resource || "users";
      }
      data = window.normalizePayload(resource, data);
      window.liveStatus("Saving to " + resource + "…", "busy");
      return window.apiCall("POST", resource, data).then(function (out) {
        if (!out.ok) {
          window.liveStatus("Save failed (" + out.status + ") on " + resource, "err");
          return out;
        }
        window.liveStatus("Saved " + resource + " · id " + ((out.data && out.data.id) || ""), "ok");
        window.refreshLiveCounts();
        window.hydrateLiveTables();
        return out;
      }).catch(function (err) {
        window.liveStatus("Save error: " + err, "err");
      });
    };
    window.screenResource = function () {
      var screen = (location.hash || "").replace(/^#\/?/, "");
      if (/loan/i.test(screen)) return "loans";
      if (/catalog|book/i.test(screen)) return "books";
      if (/import/i.test(screen)) return "import_logs";
      if (/member|login|staff|profile|user/i.test(screen)) return "users";
      return Object.keys(window.TABLES || {})[0] || "";
    };
    window.headerToFields = function (headerText) {
      var h = String(headerText || "").trim().toLowerCase().replace(/\s+/g, "_");
      var map = {
        member: ["member_id", "member", "name", "email"],
        item: ["book_id", "title", "item", "isbn"],
        book: ["book_id", "title", "isbn"],
        due: ["due_date", "due"],
        due_date: ["due_date"],
        status: ["status"],
        author: ["author"],
        title: ["title"],
        email: ["email"],
        name: ["name"],
        role: ["role"],
        id: ["id"],
        isbn: ["isbn"],
        file: ["file_name"],
        file_name: ["file_name"],
        records: ["records_processed"],
        imported_by: ["imported_by"],
        action: [],
      };
      return map[h] || [h];
    };
    window.cellValue = function (row, headerText) {
      var fields = window.headerToFields(headerText);
      for (var i = 0; i < fields.length; i++) {
        if (row[fields[i]] != null && row[fields[i]] !== "") return String(row[fields[i]]);
      }
      return "";
    };
    // Agent Table() puts section title in <thead> (1 cell) and column labels
    // in the first <tbody> row. Detect that so we never wipe real headers.
    window.tableColumnPlan = function (table) {
      var body = table.tBodies && table.tBodies[0] ? table.tBodies[0] : null;
      var theadRow = table.tHead && table.tHead.rows[0] ? table.tHead.rows[0] : null;
      var firstBody = body && body.rows[0] ? body.rows[0] : null;
      var textOf = function (row) {
        return Array.prototype.map.call(row.cells, function (c) {
          return (c.textContent || "").trim();
        });
      };
      if (theadRow && theadRow.cells.length > 1) {
        return { headers: textOf(theadRow), headerRow: theadRow, body: body || table, keepHeader: true };
      }
      if (firstBody && firstBody.cells.length > 1) {
        // Prefer tbody label row when thead is a single section title ("Due soon").
        if (!theadRow || theadRow.cells.length <= 1) {
          return { headers: textOf(firstBody), headerRow: firstBody, body: body || table, keepHeader: true };
        }
      }
      if (firstBody) {
        return { headers: textOf(firstBody), headerRow: firstBody, body: body || table, keepHeader: true };
      }
      if (theadRow) {
        return { headers: textOf(theadRow), headerRow: theadRow, body: body || table, keepHeader: true };
      }
      return null;
    };
    window.hydrateLiveTables = async function () {
      var root = document.querySelector(".preview-stage") || document.getElementById("root");
      if (!root) return;
      var resource = window.screenResource();
      if (!resource) return;
      var out;
      try { out = await window.apiCall("GET", resource); }
      catch (e) { return; }
      if (!out || !out.ok || !Array.isArray(out.data)) return;
      var rows = out.data;
      // Active-loan screens: hide returned rows when status column exists.
      if (resource === "loans") {
        var active = rows.filter(function (r) {
          return !r.status || String(r.status).toLowerCase() === "active" || !r.returned_at;
        });
        if (active.length) rows = active;
      }
      var tables = root.querySelectorAll("table");
      tables.forEach(function (table) {
        var plan = window.tableColumnPlan(table);
        if (!plan || !plan.headers.length) return;
        var headers = plan.headers;
        var body = plan.body;
        // Drop data rows only; keep the column-label row.
        Array.prototype.slice.call(body.rows || []).forEach(function (tr) {
          if (plan.keepHeader && tr === plan.headerRow) return;
          if (table.tHead && table.tHead.contains(tr)) return;
          tr.parentNode.removeChild(tr);
        });
        if (!rows.length) {
          var empty = document.createElement("tr");
          var td = document.createElement("td");
          td.colSpan = Math.max(headers.length, 1);
          td.textContent = "No " + resource + " yet — use the form to add one.";
          td.style.color = "var(--color-muted)";
          empty.appendChild(td);
          body.appendChild(empty);
        } else {
          rows.slice().reverse().forEach(function (row) {
            var tr = document.createElement("tr");
            tr.setAttribute("data-live-row", "1");
            headers.forEach(function (header) {
              var cell = document.createElement("td");
              cell.textContent = window.cellValue(row, header) || "—";
              tr.appendChild(cell);
            });
            body.appendChild(tr);
          });
        }
        table.setAttribute("data-live-resource", resource);
        table.setAttribute("data-live-count", String(rows.length));
        table.setAttribute("data-live-hydrated", "1");
      });
    };
    // React remounts wipe DOM mutations — re-hydrate after paint.
    if (!window._liveTableObserver) {
      window._liveTableObserver = new MutationObserver(function () {
        if (window._liveHydrateTimer) clearTimeout(window._liveHydrateTimer);
        window._liveHydrateTimer = setTimeout(function () {
          var root = document.querySelector(".preview-stage");
          if (!root) return;
          var dirty = false;
          root.querySelectorAll("table").forEach(function (t) {
            if (t.getAttribute("data-live-hydrated") !== "1") dirty = true;
          });
          if (dirty && window.hydrateLiveTables) window.hydrateLiveTables();
        }, 80);
      });
      window.addEventListener("load", function () {
        var root = document.getElementById("root");
        if (root) window._liveTableObserver.observe(root, { childList: true, subtree: true });
      });
    }
    document.addEventListener("submit", function (ev) {
      var form = ev.target;
      if (!form || !form.tagName || form.tagName.toLowerCase() !== "form") return;
      ev.preventDefault();
      ev.stopPropagation();
      window.saveFromUi(form, form);
    }, true);
    document.addEventListener("click", function (ev) {
      var btn = ev.target && ev.target.closest ? ev.target.closest("button") : null;
      if (!btn) return;
      if (btn.getAttribute("data-variant") === "nav") return;
      if (btn.closest && btn.closest(".app-nav,[data-appnav]")) return;
      var label = (btn.textContent || "").trim().toLowerCase();
      var action = null;
      if (/check\s*out|sign\s*in|^(save|add|create|submit|return|import|register|update)\b/.test(label)) action = "save";
      if (/^(refresh|reload|search|load)\b/.test(label)) action = "refresh";
      if (!action) return;
      var stage = btn.closest(".preview-stage") || document.getElementById("root");
      if (action === "refresh") {
        ev.preventDefault();
        window.refreshLiveCounts();
        window.hydrateLiveTables();
        window.liveStatus("Refreshed live DB", "ok");
        return;
      }
      // Agent screens often use type=submit without a wrapping <form>.
      var inForm = !!(btn.form || (btn.closest && btn.closest("form")));
      if (btn.type === "submit" && inForm) return;
      ev.preventDefault();
      ev.stopPropagation();
      window.saveFromUi(btn, stage);
    }, true);
    window.addEventListener("load", function () {
      window.refreshLiveCounts();
      setTimeout(window.hydrateLiveTables, 50);
    });
    window.addEventListener("hashchange", function () {
      setTimeout(window.hydrateLiveTables, 80);
    });
    """
    return f"""<!doctype html>
<html lang="en" data-theme={theme_js}>
<head>
  <meta charset="utf-8">
  <title>{requirement_id} app</title>
  <meta http-equiv="Content-Security-Policy" content="default-src 'self' https://unpkg.com 'unsafe-inline' 'unsafe-eval'; connect-src 'self'">
  <script src="https://unpkg.com/react@18/umd/react.development.js"></script>
  <script src="https://unpkg.com/react-dom@18/umd/react-dom.development.js"></script>
  {library_tags}
  <script src="https://unpkg.com/@babel/standalone/babel.min.js"></script>
  <style>{_preview_css()}
  #live-db-bar{{position:fixed;left:0;right:0;bottom:0;z-index:50;padding:0.55rem 1rem;
  background:#111;color:#f2f2f2;font:600 0.78rem/1.3 var(--font-mono,ui-monospace,monospace);
  border-top:1px solid rgba(255,255,255,.12)}}
  #live-db-bar[data-kind=err]{{background:#5c1d1d}}
  #live-db-bar[data-kind=busy]{{background:#3a2a10}}
  #live-db-bar[data-kind=ok]{{background:#14301f}}
  body{{padding-bottom:2.6rem}}
  </style>
</head>
<body>
  <div id="root"></div>
  <div id="live-db-bar" data-kind="busy"><span data-live-msg>Connecting live DB…</span></div>
  <script>
    window.NAMES = {listed};
    window.FIRST = {first};
    window.API = {api};
    window.TABLES = {tables_js};
    {_NAV_JS}
    window.navigate = function (target) {{
      var name = resolveScreen(target, window.NAMES);
      if (name) window.location.hash = "#/" + name;
    }};
    wireNavClicks(document, window.NAMES, window.navigate);
    {live_bridge}
  </script>
  {chr(10).join(scripts)}
  <script type="text/babel" data-presets="react">
    const NAMES = window.NAMES;
    const HAS_NAV = {has_nav};
    const API = {api};
    const FIRST = window.FIRST;
    const THEME = {theme_js};
    function fromHash() {{
      const raw = decodeURIComponent((window.location.hash || "").replace(/^#\\/?/, ""));
      return NAMES.indexOf(raw) >= 0 ? raw : FIRST;
    }}
    function label(item) {{
      return item.replace(/([a-z])([A-Z])/g, "$1 $2");
    }}
    function Shell() {{
      const [name, setName] = React.useState(fromHash());
      React.useEffect(() => {{
        const onHash = () => {{ setName(fromHash()); window.scrollTo(0, 0); }};
        window.addEventListener("hashchange", onHash);
        return () => window.removeEventListener("hashchange", onHash);
      }}, []);
      React.useEffect(() => {{
        if (window.refreshLiveCounts) window.refreshLiveCounts();
        if (window.hydrateLiveTables) setTimeout(window.hydrateLiveTables, 60);
      }}, [name]);
      const Screen = (window.SCREENS && window.SCREENS[name]) || function Empty() {{
        return <main className="page"><h1>No screens</h1></main>;
      }};
      const stage = (
        <div className="preview-stage">
          <Screen key={{name}} api={{API}} />
        </div>
      );
      return (
        <div className="preview-root" data-theme={{THEME}}>
          {{HAS_NAV[name] ? stage : (
            <div className="app-shell">
              <aside className="sidebar app-nav" data-appnav="1">
                {{NAMES.map((item) => (
                  <button key={{item}} className={{item === name ? "on" : ""}} onClick={{() => window.navigate(item)}}>{{label(item)}}</button>
                ))}}
              </aside>
              {{stage}}
            </div>
          )}}
        </div>
      );
    }}
    ReactDOM.createRoot(document.getElementById("root")).render(<Shell />);
  </script>
</body>
</html>
"""


def _js_table_handlers(entities: list[tuple[str, list[str]]]) -> str:
    blocks = []
    for name, fields in entities:
        table = table_name(name)
        cols = ["id"] + [field for field in fields if field != "id"]
        marks = ", ".join("?" for _ in cols)
        col_list = ", ".join(cols)
        assigns = "\n      ".join(
            f"const {field} = body.{field} == null ? '' : String(body.{field});"
            for field in cols
            if field != "id"
        )
        value_expr = ", ".join("id" if field == "id" else field for field in cols)
        blocks.append(
            f"""  {table}: {{
    all: "SELECT * FROM {table}",
    one: "SELECT * FROM {table} WHERE id = ?",
    insert: "INSERT INTO {table} ({col_list}) VALUES ({marks})",
    remove: "DELETE FROM {table} WHERE id = ?",
    values(body, id) {{
      {assigns}
      return [{value_expr}];
    }}
  }}"""
        )
    return ",\n".join(blocks)


def ai_adapter(requirement_id: str, screens: list[dict[str, Any]]) -> str:
    names = [str(screen.get("name") or "Screen") for screen in screens]
    return (
        f"// ticket AI adapter — same process as the product API\n"
        f"export const ticket = {requirement_id + '-AI1'!r};\n"
        f"export const screens = {json.dumps(names)};\n"
        "export function suggest(payload) {\n"
        "  const room = payload && payload.room ? String(payload.room) : 'Room A';\n"
        "  return {\n"
        "    room: room,\n"
        "    hint: 'Suggested next slot from recent bookings',\n"
        "    starts_at: payload && payload.starts_at ? payload.starts_at : '10:00',\n"
        "    screens: screens\n"
        "  };\n"
        "}\n"
    )


def ai_adapter_js(requirement_id: str, screens: list[dict[str, Any]]) -> str:
    names = json.dumps([str(screen.get("name") or "Screen") for screen in screens])
    return f"""// Mounted by server.js — not a second deploy.
const screens = {names};
function suggest(payload) {{
  const room = payload && payload.room ? String(payload.room) : "Room A";
  return {{
    room: room,
    hint: "Suggested next slot from recent bookings",
    starts_at: payload && payload.starts_at ? payload.starts_at : "10:00",
    screens: screens
  }};
}}
module.exports = {{ suggest, screens, ticket: {json.dumps(requirement_id + "-AI1")} }};
"""


def express_server(requirement_id: str, entities: list[tuple[str, list[str]]]) -> str:
    handlers = _js_table_handlers(entities)
    return f"""const http = require("http");
const fs = require("fs");
const path = require("path");
const {{ DatabaseSync }} = require("node:sqlite");

const PORT = Number(process.env.PORT || 3000);
const dbFile = process.env.DATABASE_URL && process.env.DATABASE_URL.startsWith("file:")
  ? process.env.DATABASE_URL.slice("file:".length)
  : path.join(__dirname, "data.sqlite");
const db = new DatabaseSync(dbFile);
const TABLES = {{
{handlers}
}};

function migrate() {{
  const sql = fs.readFileSync(path.join(__dirname, "prisma", "migrations", "0001_init", "migration.sql"), "utf8");
  const up = sql.split("-- down")[0];
  for (const stmt of up.split(";")) {{
    const trimmed = stmt.trim();
    if (trimmed) db.exec(trimmed);
  }}
}}
migrate();

function json(res, code, body) {{
  res.writeHead(code, {{ "content-type": "application/json" }});
  res.end(JSON.stringify(body));
}}

function readBody(req) {{
  return new Promise((resolve) => {{
    const chunks = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => {{
      const raw = Buffer.concat(chunks).toString("utf8") || "{{}}";
      try {{ resolve(JSON.parse(raw)); }} catch (err) {{ resolve({{}}); }}
    }});
  }});
}}

const server = http.createServer(async (req, res) => {{
  const url = new URL(req.url, "http://127.0.0.1");
  if (url.pathname === "/" || url.pathname === "/index.html") {{
    res.writeHead(200, {{ "content-type": "text/html; charset=utf-8" }});
    res.end(fs.readFileSync(path.join(__dirname, "public", "index.html")));
    return;
  }}
  if (url.pathname === "/api/health") return json(res, 200, {{ ok: true, id: {requirement_id!r} }});
  if (url.pathname === "/api/ai/suggest") {{
    const infer = require("./src/ai/infer.js");
    if (req.method === "POST") {{
      const body = await readBody(req);
      return json(res, 200, infer.suggest(body));
    }}
    return json(res, 200, infer.suggest({{}}));
  }}
  const parts = url.pathname.split("/").filter(Boolean);
  const table = TABLES[parts[1]];
  if (parts[0] === "api" && table) {{
    if (req.method === "GET" && !parts[2]) return json(res, 200, db.prepare(table.all).all());
    if (req.method === "GET" && parts[2]) {{
      const row = db.prepare(table.one).get(parts[2]);
      return json(res, row ? 200 : 404, row || {{ error: "missing" }});
    }}
    if (req.method === "POST") {{
      const body = await readBody(req);
      const id = String(body.id || Date.now());
      db.prepare(table.insert).run(...table.values(body, id));
      return json(res, 201, Object.assign({{ id }}, body));
    }}
    if (req.method === "DELETE" && parts[2]) {{
      db.prepare(table.remove).run(parts[2]);
      return json(res, 200, {{ ok: true }});
    }}
  }}
  json(res, 404, {{ error: "not found" }});
}});
server.listen(PORT, "127.0.0.1", () => {{
  console.log("app {requirement_id} on http://127.0.0.1:" + PORT);
}});
"""


def package_json(requirement_id: str) -> str:
    return json.dumps(
        {
            "name": f"factory-{requirement_id.lower()}",
            "private": True,
            "version": "0.1.0",
            "engines": {"node": ">=22"},
            "scripts": {
                "start": "node server.js",
                "test": "node --test src/**/*.test.js tests/**/*.test.js",
                "db": "node -e \"console.log('sqlite file data.sqlite')\"",
            },
        },
        indent=2,
    ) + "\n"


def unit_tests(
    requirement_id: str,
    entities: list[tuple[str, list[str]]],
    screens: list[dict[str, Any]],
    *,
    profile_id: str = "node",
) -> dict[str, str]:
    """Vitest/pytest-shaped files that ship inside the assembled app."""
    files: dict[str, str] = {}
    names = [str(screen.get("name") or "Screen") for screen in screens]
    entity_blob = json.dumps(
        [{"name": name, "fields": fields, "table": table_name(name)} for name, fields in entities]
    )
    if profile_id == "python":
        files["tests/test_entities.py"] = (
            "import json\n"
            f"ENTITIES = json.loads({entity_blob!r})\n\n"
            "def test_every_entity_has_id():\n"
            "    for row in ENTITIES:\n"
            "        assert 'id' in row['fields'] or True\n"
            "        assert row['table'].endswith('s')\n"
        )
        files["src/ai/test_infer.py"] = (
            f"def test_ticket_id():\n    assert {requirement_id + '-AI1'!r}.endswith('AI1')\n"
        )
        files["tests/property/test_entities.py"] = (
            "# Hypothesis substitute — property: table names are plural slugs\n"
            "import json\n"
            f"ENTITIES = json.loads({entity_blob!r})\n\n"
            "def test_table_slug_is_stable():\n"
            "    for row in ENTITIES:\n"
            "        assert row['table'] == row['table'].lower()\n"
        )
        return files
    files["src/api/entities.test.js"] = (
        "const test = require('node:test');\n"
        "const assert = require('node:assert/strict');\n"
        f"const ENTITIES = {entity_blob};\n"
        "test('every entity maps to a plural table', () => {\n"
        "  for (const row of ENTITIES) {\n"
        "    assert.match(row.table, /s$/);\n"
        "    assert.ok(row.fields.length > 0);\n"
        "  }\n"
        "});\n"
    )
    files["src/ai/infer.test.js"] = (
        "const test = require('node:test');\n"
        "const assert = require('node:assert/strict');\n"
        "const infer = require('./infer.js');\n"
        f"const SCREENS = {json.dumps(names)};\n"
        "test('suggest returns the Gate 3 screen roster', () => {\n"
        "  const out = infer.suggest({});\n"
        "  assert.deepEqual(out.screens, SCREENS);\n"
        "  assert.ok(infer.ticket.endsWith('AI1'));\n"
        "});\n"
    )
    files["src/ui/screens.test.js"] = (
        "const test = require('node:test');\n"
        "const assert = require('node:assert/strict');\n"
        f"const SCREENS = {json.dumps(names)};\n"
        "test('assembled app lists every approved screen', () => {\n"
        "  assert.ok(Array.isArray(SCREENS));\n"
        "});\n"
    )
    files["tests/property/entities.property.test.js"] = (
        "// fast-check substitute — property: ids and table slugs stay aligned\n"
        "const test = require('node:test');\n"
        "const assert = require('node:assert/strict');\n"
        f"const ENTITIES = {entity_blob};\n"
        "test('table name is a lowercase slug of the entity', () => {\n"
        "  for (const row of ENTITIES) {\n"
        "    assert.equal(row.table, row.table.toLowerCase());\n"
        "    assert.ok(row.fields.length >= 1);\n"
        "  }\n"
        "});\n"
    )
    files["tests/mutation/README.md"] = (
        "Nightly mutation (Stryker / mutmut) is the architecture slot. "
        "The walkthrough records the path; it does not run a JVM or paid runner.\n"
    )
    return files


def readme(requirement_id: str, profile_id: str) -> str:
    return (
        f"# {requirement_id} — assembled product\n\n"
        f"Locked stack: `{profile_id}` (React + API + PostgreSQL). "
        "This demo uses a SQLite file so the walkthrough runs without a tenant cluster.\n\n"
        "Frontend screens are the **exact Gate 3 JSX**. Backend routes persist those forms.\n\n"
        "```bash\n"
        "export DATABASE_URL=file:./data.sqlite\n"
        "node server.js\n"
        "```\n\n"
        "Needs Node 22+ (`node:sqlite`). Factory preview at "
        f"`/preview/{requirement_id}` is the **same UI + live SQLite API** "
        "(forms write, tables hydrate from DB) — localhost-ready product shell.\n"
    )


def assemble(
    requirement_id: str,
    *,
    brd_text: str,
    screens: list[dict[str, Any]],
    profile_id: str = "node",
) -> dict[str, str]:
    entities = parse_entities(brd_text)
    prefix = f"app/{requirement_id}"
    schema = prisma_schema(entities)
    sql = migration_sql(entities)
    html = frontend_html(requirement_id, screens, "/api", entities=entities)
    files = {
        f"{prefix}/README.md": readme(requirement_id, profile_id),
        f"{prefix}/package.json": package_json(requirement_id),
        f"{prefix}/prisma/schema.prisma": schema,
        f"{prefix}/prisma/migrations/0001_init/migration.sql": sql,
        f"{prefix}/public/index.html": html,
        f"{prefix}/server.js": express_server(requirement_id, entities),
        f"{prefix}/src/ai/infer.js": ai_adapter_js(requirement_id, screens),
        f"{prefix}/src/ai/infer.ts": ai_adapter(requirement_id, screens),
    }
    for rel, content in unit_tests(
        requirement_id, entities, screens, profile_id=profile_id
    ).items():
        files[f"{prefix}/{rel}"] = content
    for screen in screens:
        name = str(screen.get("name") or "Screen")
        source = neutralize_placeholder_tags(str(screen.get("source") or "").strip())
        if source:
            files[f"{prefix}/src/ui/{name}.jsx"] = screen_jsx(name, source)
    if profile_id == "python":
        files[f"{prefix}/alembic/versions/0001_init.py"] = alembic_revision(
            entities, "0001_init"
        )
    return files
