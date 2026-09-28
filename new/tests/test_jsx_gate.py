"""Screen JSX must compile when BRD copy has parentheses or placeholders."""

from pathlib import Path

from phase2 import jsx_gate, ui

SKILL = (Path(__file__).resolve().parent.parent / "skills/design-system.skill.md").read_text()


def test_model_screen_is_kept_when_the_function_name_differs():
    brd = (
        "## Page behaviour\n"
        "- FacilitiesCoordinatorCan: block a room for maintenance\n"
    )
    proposed = (
        "function Page(props) { return <main style={{background:'var(--color-bg)'}}>{props.children}</main>; }\n"
        "function BlockRoom() { return ( <Page><h1>Block a room</h1></Page> ); }\n"
    )
    built = ui.build_screens(
        brd,
        SKILL,
        sources={"FacilitiesCoordinatorCan": proposed},
    )
    source = built["screens"][0]["source"]
    assert "function FacilitiesCoordinatorCan" in source
    assert "Block a room" in source
    jsx_gate.compile_jsx("FacilitiesCoordinatorCan", source)


def test_later_screen_links_siblings_and_skips_booking_rows():
    brd = (
        "## Page behaviour\n"
        "- Timesheet: export this week's hours\n"
        "- Corrections: staff request a correction\n"
        "\n"
        "### Capability\n"
    )
    built = ui.build_screens(brd, SKILL)
    names = [s["name"] for s in built["screens"]]
    assert len(names) >= 2
    second = built["screens"][1]["source"]
    assert names[0] in second
    assert "10:00 Booked" not in second
    assert "Export" in built["screens"][0]["source"]


def test_model_rewrite_is_gated():
    source = (
        "function Page(props) { return <main style={{background:'var(--color-bg)'}}>{props.children}</main>; }\n"
        "function Button(props) { return <button>{props.children}</button>; }\n"
        "function Field(props) { return <label>{props.label}<input name={props.name} /></label>; }\n"
        "function Table(props) { return <table>{props.children}</table>; }\n"
        "function Screen() { return ( <Page><h1>Hi</h1><Button>Go</Button></Page> ); }\n"
    )
    out = ui.apply_model_rewrite("Screen", source, SKILL)
    assert "function Screen" in out
    jsx_gate.compile_jsx("Screen", out)


def test_fallback_emits_factory_shell():
    brd = (
        "## Page behaviour\n"
        "- StaffCanBook: pick a room and a slot\n"
        "- FacilitiesCan: approve a hold\n"
        "\n"
        "### Capability\n"
    )
    built = ui.build_screens(brd, SKILL)
    first = built["screens"][0]["source"]
    second = built["screens"][1]["source"]
    assert "function Sidebar" in first
    assert "data-theme=" in first
    assert built["screens"][0]["name"] in second
    jsx_gate.compile_jsx(built["screens"][0]["name"], first)
    assert jsx_gate.conform(first, SKILL) == []


def test_adopt_renames_last_screen_function():
    source = (
        "function Page(props) { return <main>{props.children}</main>; }\n"
        "function Button(props) { return <button>{props.children}</button>; }\n"
        "function Screen() { return ( <Page><h1>Hi</h1><Button>Go</Button></Page> ); }\n"
    )
    out = ui._adopt_model_source("StaffCanBook", source)
    assert "function StaffCanBook" in out
    jsx_gate.compile_jsx("StaffCanBook", out)


def test_rewrite_aliases_when_model_only_emits_page():
    source = (
        "function Page(props) { return <main style={{background:'var(--color-bg)'}}><h1>Hi</h1>{props.children}</main>; }\n"
        "function Button(props) { return <button>{props.children}</button>; }\n"
        "function Field(props) { return <label>{props.label}<input name={props.name} /></label>; }\n"
        "function Table(props) { return <table>{props.children}</table>; }\n"
    )
    out = ui.apply_model_rewrite("StaffCanBook", source, SKILL)
    assert "function StaffCanBook" in out
    jsx_gate.compile_jsx("StaffCanBook", out)


def test_rewrite_repairs_tokenless_form_and_adds_sidebar():
    source = (
        "function StaffCanBook() {\n"
        "  return (\n"
        "    <div>\n"
        "      <h1>Book a Room</h1>\n"
        "      <form><label>Room<input name=\"room\" /></label><button>Submit</button></form>\n"
        "    </div>\n"
        "  );\n"
        "}\n"
    )
    out = ui.apply_model_rewrite("StaffCanBook", source, SKILL)
    assert "function Page" in out
    assert "<Sidebar" in out
    assert "function StaffCanBook" in out
    jsx_gate.compile_jsx("StaffCanBook", out)
    assert jsx_gate.conform(out, SKILL) == []


def test_unknown_theme_is_rejected():
    source = (
        'function Page(props) { return <main data-theme="neon" style={{background:"var(--color-bg)"}}>{props.children}</main>; }\n'
        "function Button(props) { return <button>{props.children}</button>; }\n"
        "function Screen() { return ( <Page><h1>Hi</h1><Button>Go</Button></Page> ); }\n"
    )
    failures = jsx_gate.conform(source, SKILL)
    assert any("theme" in item for item in failures)


def test_compile_accepts_sanitized_page_copy_with_unbalanced_parens():
    brd = (
        "## Page behaviour\n"
        "- Calendar (week: Staff book one slot (or a series\n"
        "- Login: email + password (demo only — SSO out\n"
        "\n"
        "### Capability\n"
    )
    built = ui.build_screens(brd, SKILL)
    assert built["screens"]
    for screen in built["screens"]:
        jsx_gate.compile_jsx(screen["name"], screen["source"])
        assert "(" in screen["source"]
        assert screen["source"].count("(") == screen["source"].count(")")
