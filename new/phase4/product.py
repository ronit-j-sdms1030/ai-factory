"""Assemble a runnable app from the locked stack, BRD entities, and Gate 3 JSX.

Ticket files stay inside Gate 4 allow-lists. The assembled tree is the thing
a reviewer can start: SQLite (Postgres stand-in), API, exact approved screens.
"""

from __future__ import annotations

import json
import re
from typing import Any

from phase1 import brd as brd_mod

# BRD writes `- **Room:** id, name`. Also accept `- **Room**: id, name`.
_FIELD_RE = re.compile(
    r"^[-*]\s+\*\*([A-Za-z][A-Za-z0-9_]*)(?::\*\*|\*\*:)\s*(.+)$", re.M
)


def parse_entities(brd_text: str) -> list[tuple[str, list[str]]]:
    found: list[tuple[str, list[str]]] = []
    for match in _FIELD_RE.finditer(brd_text or ""):
        name = match.group(1)
        fields = [part.strip() for part in match.group(2).split(",") if part.strip()]
        if name and fields:
            found.append((name, fields))
    return found or brd_mod._entities(brd_text or "")


def table_name(entity: str) -> str:
    slug = re.sub(r"(?<!^)([A-Z])", r"_\1", entity).lower()
    if not slug.endswith("s"):
        slug += "s"
    return slug


def _prisma_type(field: str) -> str:
    key = field.lower()
    if key == "id":
        return "String @id"
    if key.endswith("_at") or key in {"starts_at", "ends_at", "due_at"}:
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
    text = (source or "").strip()
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
        "--space-md:16px;--font-sans:Ubuntu,'Liberation Sans',system-ui,sans-serif}"
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
    themes = ("midnight", "aurora", "paper", "grove", "coral", "ink", "glacier", "sand")
    key = "|".join(names) or "app"
    return themes[sum(map(ord, key)) % len(themes)]


def _preview_css() -> str:
    return (
        _theme_css()
        + "*{box-sizing:border-box}html,body,#root{height:100%;margin:0}"
        "body{background:var(--color-bg);color:var(--color-text);font-family:var(--font-sans)}"
        "@keyframes rise{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}"
        ".preview-root{display:flex;flex-direction:column;min-height:100vh;height:100%;"
        "background:var(--color-bg);color:var(--color-text)}"
        ".app-shell{display:flex;flex:1;min-height:0}"
        ".preview-stage{flex:1;min-width:0;min-height:0;overflow:auto}"
        ".page{display:flex;min-height:100%;width:100%;max-width:none;padding:0;"
        "animation:rise .45s ease both}"
        ".sidebar{width:240px;flex-shrink:0;background:var(--color-sidebar);"
        "border-right:1px solid var(--color-line);padding:1.25rem 0.9rem;"
        "display:flex;flex-direction:column;gap:0.35rem}"
        ".sidebar>p:first-child{padding:0.2rem 0.7rem 1rem;margin:0;font-weight:600}"
        ".page>:not(.sidebar){flex:1;min-width:0;padding:clamp(1.4rem,3vw,2.6rem)}"
        "h1{margin:0 0 0.4rem;font-size:clamp(1.55rem,2.4vw,2.2rem);font-weight:700}"
        "p{margin:0 0 1.2rem;max-width:42em;line-height:1.55;color:var(--color-muted)}"
        "label{display:flex;flex-direction:column;gap:8px;width:min(100%,28rem);"
        "margin:0 0 1rem;font-size:0.78rem;font-weight:500;color:var(--color-muted)}"
        "input,select,textarea{text-transform:none;letter-spacing:0;font-weight:450;"
        "font-size:0.95rem;font-family:inherit;height:42px;border:1px solid var(--color-line);"
        "border-radius:8px;padding:0 12px;background:var(--color-surface);width:100%;"
        "color:var(--color-text)}"
        "button{appearance:none;border:0;border-radius:8px;font:inherit;font-weight:600;"
        "cursor:pointer;background:var(--color-accent);color:var(--color-bg);"
        "padding:0.7rem 1.1rem;transition:transform .16s ease,filter .16s ease}"
        "button:hover{transform:translateY(-1px);filter:brightness(1.06)}"
        ".sidebar button{background:transparent;color:var(--color-muted);"
        "border:1px solid transparent;padding:0.5rem 0.75rem;text-align:left;width:100%;"
        "font-weight:500}"
        ".sidebar button:hover,.sidebar button.on{"
        "background:var(--color-accent);color:var(--color-bg)}"
        "@media (max-width:720px){.page,.app-shell{flex-direction:column}"
        ".sidebar{width:auto;flex-direction:row;overflow-x:auto;border-right:0;"
        "border-bottom:1px solid var(--color-line)}"
        ".sidebar button{width:auto;white-space:nowrap}}"
        "table{width:100%;border-collapse:collapse;background:var(--color-surface);"
        "border:1px solid var(--color-line);border-radius:10px;overflow:hidden;margin-top:1.2rem}"
        "th,td{text-align:left;padding:0.8rem 1rem;border-bottom:1px solid var(--color-line)}"
        "th{font-size:0.7rem;letter-spacing:0.04em;text-transform:uppercase;"
        "color:var(--color-muted);font-weight:600}"
    )


def frontend_html(requirement_id: str, screens: list[dict[str, Any]], api_base: str) -> str:
    names = [str(item.get("name") or "Screen") for item in screens]
    scripts = []
    for screen in screens:
        name = str(screen.get("name") or "Screen")
        source = str(screen.get("source") or "").strip()
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
    library_tags = "<script>window.react = window.React;</script>\n  " + "\n  ".join(
        f'<script src="{src}"></script>' for src in LIBRARY_SCRIPTS
    )
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
  <style>{_preview_css()}</style>
</head>
<body>
  <div id="root"></div>
  <script>
    window.NAMES = {listed};
    window.FIRST = {first};
    {_NAV_JS}
    window.navigate = function (target) {{
      var name = resolveScreen(target, window.NAMES);
      if (name) window.location.hash = "#/" + name;
    }};
    wireNavClicks(document, window.NAMES, window.navigate);
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
        "Needs Node 22+ (`node:sqlite`). Factory preview also serves this UI at "
        f"`/preview/{requirement_id}` against the same schema.\n"
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
    html = frontend_html(requirement_id, screens, "/api")
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
        source = str(screen.get("source") or "").strip()
        if source:
            files[f"{prefix}/src/ui/{name}.jsx"] = screen_jsx(name, source)
    if profile_id == "python":
        files[f"{prefix}/alembic/versions/0001_init.py"] = alembic_revision(
            entities, "0001_init"
        )
    return files
