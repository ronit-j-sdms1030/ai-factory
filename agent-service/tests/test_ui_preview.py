"""Assembling the generated screens into one viewable page."""

from __future__ import annotations

from app.ui_preview import CSP, build_preview

SCREENS = {
    "screens": [
        {"name": "ClockInterface", "route": "/clock", "purpose": "Clock in and out",
         "source": "function ClockInterface() { return <div>Clock</div>; }"},
        {"name": "MyLeave", "route": "/leave/my-leave", "purpose": "Leave balances",
         "source": "function MyLeave() { return <div>Leave</div>; }"},
    ],
    "clarifications": [],
}


class TestAssembly:
    def test_every_screen_source_is_included(self):
        page = build_preview(SCREENS, "WorkPulse")
        assert "function ClockInterface()" in page
        assert "function MyLeave()" in page

    def test_sources_are_carried_as_data_not_inlined_code(self):
        """A malformed source must not break the page meant to report it."""
        page = build_preview({"screens": [{"name": "Truncated", "route": "/t",
                                           "source": "const styles = { boxShadow: '0 1px ANIMATION"}]}, "t")
        # No babel-compiled tags at all: screen sources travel as JSON and are
        # compiled one at a time at runtime, and the shell is plain JS. A
        # source that will not parse cannot stop the page loading.
        assert page.count('<script type="text/babel"') == 0
        assert "0 1px ANIMATION" in page

    def test_each_screen_is_compiled_individually(self):
        page = build_preview(SCREENS, "t")
        assert "Babel.transform(__SOURCES[i].source" in page
        assert "__ERRORS[name]" in page

    def test_screens_are_navigable_by_name_and_route(self):
        page = build_preview(SCREENS, "WorkPulse")
        assert '"name": "ClockInterface"' in page or '"name":"ClockInterface"' in page
        assert "/leave/my-leave" in page

    def test_react_and_babel_are_loaded(self):
        """The screens are JSX with no build step, so Babel has to compile in-page."""
        page = build_preview(SCREENS, "WorkPulse")
        assert "react@18" in page and "react-dom@18" in page and "babel" in page

    def test_the_shell_needs_no_compilation(self):
        """It has to work even when every screen is broken."""
        page = build_preview(SCREENS, "WorkPulse")
        assert "React.createElement" in page

    def test_a_screen_with_no_source_is_skipped(self):
        page = build_preview({"screens": [*SCREENS["screens"], {"name": "Broken", "route": "/x", "source": ""}]}, "t")
        assert "Broken:" not in page

    def test_a_non_identifier_name_cannot_break_the_page(self):
        """A stray name would otherwise be a syntax error taking every screen with it."""
        page = build_preview({"screens": [*SCREENS["screens"], {"name": "not a name", "route": "/y", "source": "x"}]}, "t")
        assert "not a name" not in page

    def test_each_screen_gets_its_own_scope(self):
        """Two screens declaring `const styles` is legal apart, fatal together.

        Concatenated into one script it is a redeclaration — a syntax error
        that takes down every screen at once, with nothing to say which one
        caused it.
        """
        clashing = {"screens": [
            {"name": "A", "route": "/a", "source": "const styles = {x:1}; function A() { return <div/>; }"},
            {"name": "B", "route": "/b", "source": "const styles = {y:2}; function B() { return <div/>; }"},
        ]}
        page = build_preview(clashing, "t")
        assert '"name": "A"' in page and '"name": "B"' in page
        assert page.count("Babel.transform") == 1   # one compiler loop, not one per screen

    def test_components_are_published_for_cross_screen_references(self):
        page = build_preview(SCREENS, "t")
        assert "Object.assign(globalThis, __COMPONENTS)" in page


class TestEmptyAndSafety:
    def test_no_screens_yet_says_so_rather_than_rendering_blank(self):
        page = build_preview({"screens": []}, "WorkPulse")
        assert "No screens have been generated" in page

    def test_the_title_is_escaped(self):
        page = build_preview({"screens": []}, "<script>alert(1)</script>")
        assert "<script>alert(1)</script>" not in page

    def test_the_sandbox_policy_denies_network_access(self):
        """Generated code is untrusted: it may render, it may not phone home."""
        assert "sandbox allow-scripts" in CSP
        assert "connect-src 'none'" in CSP
        assert "default-src 'none'" in CSP


class TestOneBrokenScreenIsContained:
    """A truncated screen must cost one screen, not all of them.

    Screens that hit the token ceiling arrive cut off mid-string. That is a
    syntax error, so a shared script tag means the parser rejects everything
    and the whole preview renders blank — observed on a real artifact whose
    source ended at `boxShadow: '0 1px ANIMATION`.
    """

    def test_a_failed_screen_is_named_in_the_nav(self):
        page = build_preview(SCREENS, "t")
        assert "class = 'broken'" in page or "b.className = 'broken'" in page

    def test_a_truncated_source_still_leaves_the_others_listed(self):
        broken = {"screens": [
            SCREENS["screens"][0],
            {"name": "Truncated", "route": "/t", "source": "const styles = { boxShadow: '0 1px ANIMATION"},
        ]}
        page = build_preview(broken, "t")
        assert '"name": "ClockInterface"' in page and '"name": "Truncated"' in page


class TestJsxRuntime:
    """Babel must not inject an import into its own output.

    The react preset defaults to the automatic JSX runtime, which emits
    `import { jsx } from "react/jsx-runtime"` at the top of the compiled code.
    Evaluating that throws "Cannot use import statement outside a module", so
    every screen that compiled *correctly* failed anyway — and looked exactly
    like a screen that was genuinely broken.
    """

    def test_the_classic_runtime_is_requested(self):
        page = build_preview(SCREENS, "t")
        assert "runtime: 'classic'" in page

    def test_react_is_global_for_createElement(self):
        page = build_preview(SCREENS, "t")
        assert "react@18/umd/react.development.js" in page


class TestThinPlansAreUsable:
    """A model may return screen names and nothing else, and often does.

    Requiring route/purpose/keyElements lost entire plans to a provider-side
    400, so they are optional — which means the agent has to cope when they
    arrive empty rather than generating screens with no route at all.
    """

    def test_a_route_is_derived_from_the_name(self):
        from app.agents.ui import _fill_blanks
        from app.schemas import ScreenOutline, UIPlan

        plan = UIPlan(screens=[ScreenOutline(name="ReturnsDashboard")], clarifications=[])
        _fill_blanks(plan)
        assert plan.screens[0].route == "/returns-dashboard"

    def test_an_existing_route_is_left_alone(self):
        from app.agents.ui import _fill_blanks
        from app.schemas import ScreenOutline, UIPlan

        plan = UIPlan(screens=[ScreenOutline(name="X", route="/custom")], clarifications=[])
        _fill_blanks(plan)
        assert plan.screens[0].route == "/custom"


class TestScreenNamesBecomeIdentifiers:
    """The name is a JavaScript identifier, not a label.

    The preview registers and looks up each component by name, so "Leave
    Request" — which models return despite being asked for PascalCase — is
    dropped from the very preview it was generated for.
    """

    def test_spaces_are_removed(self):
        from app.agents.ui import _pascal_case

        assert _pascal_case("Leave Request") == "LeaveRequest"

    def test_punctuation_is_removed(self):
        from app.agents.ui import _pascal_case

        assert _pascal_case("My-Leave (v2)") == "MyLeaveV2"

    def test_an_already_valid_name_is_unchanged(self):
        from app.agents.ui import _pascal_case

        assert _pascal_case("ClockInOut") == "ClockInOut"

    def test_an_unusable_name_becomes_empty_rather_than_invalid(self):
        from app.agents.ui import _pascal_case

        assert _pascal_case("   ") == ""


class TestComponentNameDrift:
    """The plan's name and the source's name are not always the same.

    A screen planned as ClockInOut arrives defining ClockInOutScreen, and
    looking it up by the planned name reported "compiled, but defined no
    component called ClockInOut" — for code that was perfectly good.
    """

    def test_a_differently_named_component_is_found(self):
        from app.ui_preview import _component_name

        assert _component_name("function ClockInOutScreen() {}", "ClockInOut") == "ClockInOutScreen"

    def test_a_matching_name_is_preferred(self):
        from app.ui_preview import _component_name

        source = "function Helper() {}\nfunction Dashboard() {}"
        assert _component_name(source, "Dashboard") == "Dashboard"

    def test_an_arrow_component_is_recognised(self):
        from app.ui_preview import _component_name

        assert _component_name("const Dashboard = () => {};", "Other") == "Dashboard"

    def test_the_planned_name_is_the_fallback(self):
        from app.ui_preview import _component_name

        assert _component_name("// nothing declared", "Fallback") == "Fallback"

    def test_the_component_name_reaches_the_page(self):
        page = build_preview({"screens": [
            {"name": "ClockInOut", "route": "/c", "source": "function ClockInOutScreen(){ return null; }"}
        ]}, "t")
        assert '"component": "ClockInOutScreen"' in page
