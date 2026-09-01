"""Assemble the UI agent's screens into one runnable page.

The agent writes each screen as a self-contained React function component with
no imports and no exports, on the explicit promise that they are "assembled
into a single preview file where React and ReactDOM are already global". This
is the other half of that promise — without it the screens were generated,
committed as .jsx source, and viewable only by reading the code.

**Each screen is compiled on its own, at runtime.** Concatenating them into a
single script fails two ways, both seen on real output: two screens declaring
``const styles`` is a redeclaration, and a screen truncated at the token
ceiling ends mid-string. Both are *syntax* errors, so the parser rejects the
whole bundle and all twelve screens vanish together with no clue which one was
at fault. Separate ``<script>`` tags are not enough either — Babel stops
processing after one throws. Compiling each source individually inside a
try/catch is the only arrangement where a bad screen costs exactly itself and
says so.

Nothing is executed server-side. The page is static HTML served into a
sandboxed iframe under the same Content-Security-Policy the JavaScript
project demo uses: generated code is treated as untrusted, exactly like any
third-party static site.
"""

from __future__ import annotations

import html
import json
import re
from typing import Any

# React and Babel are fetched from unpkg, matching the project-demo sandbox's
# CSP allowance. Babel is needed because the screens are JSX, not compiled JS.
_CDN = "https://unpkg.com"

_SHELL = """<!doctype html>
<meta charset="utf-8">
<title>{title} — screens</title>
<script src="{cdn}/react@18/umd/react.development.js" crossorigin></script>
<script src="{cdn}/react-dom@18/umd/react-dom.development.js" crossorigin></script>
<script src="{cdn}/@babel/standalone/babel.min.js"></script>
<style>
  :root {{ color-scheme: light; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; font: 15px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; background: #f6f6f4; }}
  #nav {{ display: flex; gap: .25rem; flex-wrap: wrap; padding: .6rem .8rem;
         background: #1f2430; position: sticky; top: 0; z-index: 999; }}
  #nav button {{ font: inherit; font-size: 13px; padding: .35rem .7rem; border-radius: 6px;
                 border: 1px solid #3a4152; background: #2a3040; color: #cdd3e0; cursor: pointer; }}
  #nav button[aria-current="true"] {{ background: #c2603f; border-color: #c2603f; color: #fff; }}
  #nav button.broken {{ color: #8a6a6a; border-color: #5a3a3a; text-decoration: line-through; }}
  #route {{ padding: .35rem .85rem; font: 12px ui-monospace, monospace; color: #7c8598; background: #171b24; }}
  .fail {{ margin: 1.5rem; padding: 1rem; border: 1px solid #d9534f; border-radius: 8px;
           background: #fff; color: #a33; white-space: pre-wrap; font: 13px ui-monospace, monospace; }}
</style>
<div id="nav"></div>
<div id="route"></div>
<div id="root"></div>

<script>
  var __SCREENS = {manifest};
  var __SOURCES = {sources};
  var __COMPONENTS = {{}};
  var __ERRORS = {{}};
</script>

<script>
// Compile each screen alone. A screen that will not parse is recorded and
// skipped; every other screen still renders.
(function () {{
  for (var i = 0; i < __SOURCES.length; i++) {{
    var name = __SOURCES[i].name;
    // What the source actually calls its component; see _component_name.
    var declared = __SOURCES[i].component || name;
    try {{
      // The classic runtime, deliberately. Babel's react preset defaults to
      // the automatic one, which *injects* `import {{ jsx }} from
      // "react/jsx-runtime"` into its own output — and eval of an import is
      // "Cannot use import statement outside a module". Every screen that
      // compiled perfectly then failed on that, making good screens look
      // identical to broken ones. Classic emits React.createElement, and
      // React is already global here.
      var compiled = Babel.transform(__SOURCES[i].source, {{
        presets: [['react', {{ runtime: 'classic' }}]]
      }}).code;
      // Direct eval, so the screen's own declarations stay in this scope
      // instead of leaking to the page, and the registration line below can
      // still see the component it just defined.
      eval(compiled + '\\n;__COMPONENTS[' + JSON.stringify(name) + '] = typeof ' + declared + " !== 'undefined' ? " + declared + ' : null;');
      if (!__COMPONENTS[name]) __ERRORS[name] = 'compiled, but defined no component called ' + name;
    }} catch (e) {{
      __ERRORS[name] = String((e && e.message) || e);
    }}
  }}
  // Cross-screen references resolve at render time, so publishing the registry
  // after every screen is defined lets one screen reference another.
  Object.assign(globalThis, __COMPONENTS);
}})();
</script>

<script>
// Plain JavaScript, deliberately. Babel's automatic pass over text/babel tags
// did not reliably run here, and the shell is the one part that must work
// even when every screen is broken — so it uses React.createElement directly
// and needs no compilation at all.
(function () {{
  var e = React.createElement;

  function Preview() {{
    var state = React.useState(0), i = state[0], setI = state[1];
    var current = __SCREENS[i];

    React.useEffect(function () {{
      var nav = document.getElementById('nav');
      nav.innerHTML = '';
      __SCREENS.forEach(function (s, n) {{
        var b = document.createElement('button');
        b.textContent = s.name;
        if (__ERRORS[s.name]) {{ b.className = 'broken'; b.title = __ERRORS[s.name]; }}
        if (n === i) b.setAttribute('aria-current', 'true');
        b.onclick = function () {{ setI(n); }};
        nav.appendChild(b);
      }});
      document.getElementById('route').textContent = current ? current.route : '';
    }}, [i]);

    if (!current) return e('div', {{ className: 'fail' }}, 'No screens.');
    var Cmp = __COMPONENTS[current.name];
    if (!Cmp) {{
      return e('div', {{ className: 'fail' }},
        current.name + ' could not be loaded.\\n\\n' + (__ERRORS[current.name] || 'unknown error'));
    }}
    return e(Cmp);
  }}

  // A screen that throws while rendering must not blank the rest.
  var ErrorCatcher = class extends React.Component {{
    constructor(p) {{ super(p); this.state = {{ error: null }}; }}
    static getDerivedStateFromError(error) {{ return {{ error: error }}; }}
    componentDidUpdate(prev) {{ if (prev.children !== this.props.children) this.setState({{ error: null }}); }}
    render() {{
      if (this.state.error) {{
        return e('div', {{ className: 'fail' }}, String(this.state.error.stack || this.state.error));
      }}
      return this.props.children;
    }}
  }};

  ReactDOM.createRoot(document.getElementById('root')).render(e(ErrorCatcher, null, e(Preview)));
}})();
</script>
"""

# Matches the project-demo sandbox: generated code runs with no network reach
# and no access to the parent page.
CSP = (
    "sandbox allow-scripts; default-src 'none'; "
    "script-src 'unsafe-inline' 'unsafe-eval' https://unpkg.com; "
    "style-src 'unsafe-inline' https:; img-src data: https:; font-src https:; connect-src 'none'"
)


def build_preview(ui: dict[str, Any], title: str = "Untitled") -> str:
    """One HTML page showing every screen, switchable from a nav bar."""
    screens = [
        s for s in (ui.get("screens") or [])
        if s.get("source") and str(s.get("name", "")).isidentifier()
    ]
    if not screens:
        return _empty(title)

    return _SHELL.format(
        title=html.escape(title),
        cdn=_CDN,
        manifest=json.dumps([{"name": s["name"], "route": s.get("route", "")} for s in screens]),
        # Carried as data rather than inlined as code, so a malformed source
        # cannot break the page that is meant to report it as malformed.
        sources=json.dumps([
            {"name": s["name"], "component": _component_name(s["source"], s["name"]), "source": s["source"]}
            for s in screens
        ]),
    )


# `function Dashboard(`, `const Dashboard = (`, `class Dashboard extends`
_DECLARES = re.compile(
    r"^\s*(?:function\s+([A-Z]\w*)\s*\(|"
    r"(?:const|let|var)\s+([A-Z]\w*)\s*=\s*(?:\(|function|React\.memo|memo)|"
    r"class\s+([A-Z]\w*)\s+extends)",
    re.MULTILINE,
)


def _component_name(source: str, planned: str) -> str:
    """What the source actually calls its component.

    The plan's name and the source's name drift — a screen planned as
    ClockInOut arrives defining ClockInOutScreen, and looking it up by the
    planned name found nothing, reporting "compiled, but defined no component
    called ClockInOut" for code that was perfectly good. The planned name wins
    when the source agrees or declares nothing recognisable.
    """
    declared = [next(g for g in m.groups() if g) for m in _DECLARES.finditer(source)]
    if planned in declared:
        return planned
    return declared[0] if declared else planned


def _empty(title: str) -> str:
    return (
        "<!doctype html><meta charset='utf-8'>"
        f"<title>{html.escape(title)}</title>"
        "<body style=\"font:15px system-ui;padding:2rem;color:#555\">"
        "No screens have been generated for this requirement yet."
    )
