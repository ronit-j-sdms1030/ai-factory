"""Screen JSX must compile when BRD copy has parentheses or placeholders."""

from pathlib import Path

from phase2 import coverage, jsx_gate, ui

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


def test_component_name_does_not_mint_numeric_duplicates():
    used: set[str] = set()
    first = coverage.component_name({"id": "LoginScreen", "description": "sign in"}, used)
    second = coverage.component_name({"id": "Login Screen", "description": "auth again"}, used)
    assert first == "LoginScreen"
    assert second == "LoginScreen"
    assert "LoginScreen2" not in used


def test_fallback_layouts_differ_by_screen_role():
    brd = (
        "## Page behaviour\n"
        "- LoginScreen: staff sign in with work email\n"
        "- StaffDashboard: loan counts and overdue chart\n"
        "- CatalogueManagement: browse and search the catalogue\n"
        "- LoanManagement: check out books to members\n"
        "- MemberProfileView: member profile detail\n"
        "- MemberPortal: member portal home\n"
        "- DataImportView: CSV catalogue import\n"
    )
    built = ui.build_screens(brd, SKILL)
    by_name = {s["name"]: s["source"] for s in built["screens"]}
    assert "<Table" not in by_name["LoginScreen"]
    assert "Sign in" in by_name["LoginScreen"]
    assert "Needs attention" in by_name["StaffDashboard"]
    assert "Results" in by_name["CatalogueManagement"]
    assert "New loan" in by_name["LoanManagement"]
    assert "<Table" not in by_name["MemberProfileView"]
    assert "Quick actions" in by_name["MemberPortal"]
    assert "Preview mapping" in by_name["DataImportView"]
    # Layout fingerprints must not all collapse to one shell.
    fingerprints = {
        name: ("Table" in src, "Sign in" in src, "Needs attention" in src, "New loan" in src)
        for name, src in by_name.items()
    }
    assert len(set(fingerprints.values())) >= 4
    for screen in built["screens"]:
        jsx_gate.compile_jsx(screen["name"], screen["source"])
        assert jsx_gate.conform(screen["source"], SKILL) == []


def test_screen_role_classifies_library_pages():
    assert ui.screen_role("LoginScreen", {"description": "Auth entry"}) == "auth"
    assert ui.screen_role("StaffDashboard", {"description": "Counts and chart"}) == "dashboard"
    assert ui.screen_role("MemberPortal", {"description": "Member portal home"}) == "portal"
    assert ui.screen_role("DataImportView", {"description": "CSV import"}) == "import"
    # "author" must not trip the auth needle
    assert (
        ui.screen_role(
            "CatalogueManagement",
            {"description": "searchable table (title, author, ISBN, status)"},
        )
        == "catalog"
    )


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


def test_placeholder_tags_are_plain_text_for_every_screen():
    brd = "## Page behaviour\n- AgencyHome: show the agency\n"
    proposed = (
        "function AgencyHome() {\n"
        "  return (\n"
        '    <Page data-theme="sand" style={{background:"var(--color-bg)"}}>\n'
        "      <h1>About <LOCATION> and Heritage</h1>\n"
        "      <span><LOCATIONS> Travels</span>\n"
        '      <Button type="button">Go</Button>\n'
        "    </Page>\n"
        "  );\n"
        "}\n"
    )
    built = ui.build_screens(brd, SKILL, sources={"AgencyHome": proposed})
    source = built["screens"][0]["source"]
    assert "<LOCATION" not in source
    assert "<LOCATIONS" not in source
    assert "LOCATION" in source
    assert "LOCATIONS" in source
    assert "<Button" in source
    assert "<Page" in source
    jsx_gate.compile_jsx(built["screens"][0]["name"], source)


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
