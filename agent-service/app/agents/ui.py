"""UI agent — approved BRD to reviewable screens, before any code exists.

New capability, not a port. The JavaScript pipeline has no equivalent: its
nearest artefact is the project-demo synthesis, which runs *after* all code is
generated, so the thing meant to gate code generation is produced by it.
SoW 12.0 requires the opposite — "code generation for the affected scope
cannot start until UI approval is recorded".

Clarifications are a first-class output rather than something the model
resolves silently, per SoW 11.0: "The agent raises clarification requests
where the BRD is ambiguous."

**Planned first, then written one screen at a time.** The original single call
asked for every screen's React source at once and overflowed its token
ceiling, truncating the JSON mid-string. That fails as a parse error rather
than a short answer, so the entire design was lost rather than the last
screen — an eight-page BRD reliably produced nothing at all. Splitting the
work means each call is small enough to finish, the screens generate
concurrently instead of serially, and one bad screen costs one screen.
"""

from __future__ import annotations

import json
import logging
import re
from concurrent.futures import ThreadPoolExecutor

from langsmith import traceable

from .. import config, llm, prompts
from . import model_for
from ..schemas import ScreenSource, UIPlan
from ..state import PipelineState

log = logging.getLogger(__name__)

# Screens are independent calls, so they overlap. The cap sets how many waves
# the run takes: at 4, an eight-screen design is two waves of ~110s each, and
# the wall time is dominated by waiting rather than by generating. Raised to 8
# because nothing in a screen depends on another screen — the roster of names
# is computed up front — so the only real constraint is the provider's rate
# limit, which this is still well inside.
_MAX_CONCURRENT_SCREENS = 8




@traceable(name="UI Agent")
def ui_agent(state: PipelineState) -> dict:
    brd = state["brd"]

    plan = _plan_screens(brd, model_for(state, "uiPlan"))
    if not plan.screens:
        raise RuntimeError("the UI agent planned no screens")
    _fill_blanks(plan)

    # Every screen is told what the others are called, so a cross-screen
    # reference resolves to something that exists. Without it each call is
    # blind to its siblings and invents navigation targets that were never
    # generated.
    roster = ", ".join(f"{s.name} ({s.route})" for s in plan.screens)
    screen_model = model_for(state, "ui")

    # Both read once for the whole generation rather than once per screen
    # call, so every screen in this run is written against the exact same
    # design system and rule set — and so the versions recorded below are the
    # ones that actually applied.
    learned_text, learned_ids = _learned()
    skill_text, skill_version = _skill_file()

    with ThreadPoolExecutor(max_workers=_MAX_CONCURRENT_SCREENS) as pool:
        sources = list(
            pool.map(
                lambda s: _write_screen(brd, s, roster, screen_model, learned_text, skill_text),
                plan.screens,
            )
        )

    screens: list[dict] = []
    failed: list[str] = []
    for outline, source in zip(plan.screens, sources):
        if source is None:
            failed.append(outline.name)
            continue
        screens.append(
            {
                "name": outline.name,
                "route": outline.route,
                "purpose": outline.purpose,
                "source": source,
            }
        )

    if not screens:
        raise RuntimeError(f"every screen failed to generate ({len(failed)} attempted)")

    # A screen that could not be written is reported to the reviewer, not
    # dropped quietly. Same rule the BRD agent follows for unresolved critique
    # findings: a failed generation degrades to "flagged for a human".
    clarifications = list(plan.clarifications)
    if failed:
        clarifications.append(
            "These screens could not be generated and need another pass: " + ", ".join(failed)
        )

    return {
        "ui": {
            "screens": screens,
            "clarifications": clarifications,
            "learnedRuleIds": learned_ids,
            "skillFileVersion": skill_version,
            "skillFile": skill_text,
            # SoW 7.0 audit fields, recorded where they cannot drift from what
            # actually ran: the models used and the prompt versions in force.
            "provenance": {
                "planModel": model_for(state, "uiPlan"),
                "screenModel": screen_model,
                "promptVersions": prompts.versions("ui"),
            },
        }
    }


# A screen arrives as a real file would — `import React from 'react'` at the
# top, sometimes `export default` at the bottom. The prompt says not to, and a
# code-specialised model does it anyway: every one of eleven screens failed
# with "Cannot use import statement outside a module" while the React itself
# was clean. Instructing harder is the losing move here; these models are
# trained on files that always have imports.
def strip_module_syntax(source: str) -> str:
    """Remove import/export syntax from a generated screen.

    Only lines that *begin* with import/export are touched, so a string or
    comment mentioning either word survives. Declarations are always kept —
    ``export default function Dashboard()`` becomes ``function Dashboard()``,
    because deleting that line would take the component with it. Only a bare
    ``export default Dashboard;`` is dropped outright, the preview having
    already found the component by name.
    """
    kept: list[str] = []
    for line in source.splitlines():
        stripped = line.lstrip()

        if stripped.startswith(("import ", "import{", "import(")):
            continue

        if stripped.startswith("export default"):
            rest = stripped[len("export default"):].lstrip()
            # A declaration must keep its body; a bare re-export is noise.
            if rest.startswith(("function", "class", "async")):
                indent = line[: len(line) - len(stripped)]
                kept.append(indent + rest)
            continue

        if stripped.startswith("export "):
            line = line.replace("export ", "", 1)

        kept.append(line)

    return "\n".join(kept)


def _pascal_case(name: str) -> str:
    """'Leave Request' -> 'LeaveRequest'. Anything unusable becomes empty."""
    parts = re.findall(r"[A-Za-z][A-Za-z0-9]*", name or "")
    joined = "".join(p[:1].upper() + p[1:] for p in parts)
    return joined if joined.isidentifier() else ""


def _fill_blanks(plan: UIPlan) -> None:
    """Supply what a thin plan left out.

    Every field but ``name`` is optional, because requiring them lost whole
    plans to a 400. That trade means a model can hand back names and nothing
    else — gpt-oss-120b returns exactly that — so the route is derived here
    rather than left empty. Purpose and key elements cannot be invented
    honestly; a screen without them is still written from the BRD's objective,
    data model and the roster of its siblings.
    """
    for screen in plan.screens:
        # The name becomes a JavaScript identifier — it is how the preview
        # registers and looks up the component. Models return "Leave Request"
        # despite being asked for PascalCase, and an invalid identifier means
        # the screen is silently dropped from the preview it was generated for.
        screen.name = _pascal_case(screen.name) or "Screen"
        if not screen.route:
            # ReturnsDashboard -> /returns-dashboard
            screen.route = "/" + re.sub(r"(?<!^)(?=[A-Z])", "-", screen.name).lower()
        if not screen.purpose:
            screen.purpose = f"The {screen.name} screen."


def _plan_screens(brd: dict, model: str) -> UIPlan:
    """Decide the screen set. Small output, so this call is cheap and reliable.

    Deliberately its own model. Planning is a schema-following task, not a
    coding one, and two models have failed it while handling their own stage
    fine — see UI_PLAN_MODEL in config for what each did.
    """
    return llm.call_structured(
        model=model,
        schema=UIPlan,
        max_tokens=4000,
        retries=1,
        timeout=120.0,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are the UI agent inside Stark Digital's AI Software Factory. Plan the screens "
                    "for this approved BRD, before any backend code exists. Do not write any code yet — "
                    "name the screens and say what is on each.\n\n"
                    "Fill EVERY field for EVERY screen: name, route, purpose, and keyElements. A screen "
                    "with only a name cannot be built from — keyElements in particular is what the next "
                    "step writes the code against, so name the real tables, forms, filters and actions.\n\n"
                    "Derive them from pageBehavior and dataModel.\n\n"
                    + prompts.text("ui.productShape")
                    + "\n\n"
                    "Where the BRD is genuinely ambiguous about interface behaviour, record a "
                    "clarification rather than guessing silently."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Objective:\n{brd['objective']}\n\n"
                    f"Pages:\n{json.dumps(brd['pageBehavior'], indent=2)}\n\n"
                    f"Data model:\n{json.dumps(brd['dataModel'], indent=2)}\n\n"
                    f"User flow:\n{json.dumps(brd['userFlow'], indent=2)}"
                ),
            },
        ],
    )


def _learned() -> tuple[str, list[str]]:
    """Rules reviewers have already taught this agent, as a prompt fragment.

    Returns the fragment alongside the ids it was built from, so the caller
    can record exactly which instructions produced a given generation rather
    than only that "some rules" applied — the active set moves on, but an
    approved screen should not silently change what it is attributed to.

    Best-effort by design: a screen that generates without its accumulated
    house style is worth far more than no screen at all, so a failure to read
    them is logged and ignored.
    """
    try:
        from ..lessons import active_ids, prompt_section

        section = prompt_section("ui")
        if not section:
            return "", []
        return f"\n\n{section}", active_ids("ui")
    except Exception as exc:  # noqa: BLE001
        log.warning("could not load learned rules: %s", exc)
        return "", []


def _skill_file() -> tuple[str, int]:
    """The design system every screen in this run must follow, and its version.

    Best-effort in the same way learned rules are, but with a stronger
    fallback: an unreachable database returns the built-in default rather than
    an empty string, because generating screens with no design system at all
    is the failure this replaces.
    """
    try:
        from ..design_system import DEFAULT_SKILL, current, prompt_section

        return prompt_section(), current()["version"]
    except Exception as exc:  # noqa: BLE001
        log.warning("could not load the design-system skill file: %s", exc)
        from ..design_system import DEFAULT_SKILL

        return DEFAULT_SKILL, 0


def _write_screen(
    brd: dict, outline, roster: str, model: str, learned_text: str = "", skill_text: str = ""
) -> str | None:
    """Generate one screen's source. Returns None so one failure costs one screen."""
    try:
        result = llm.call_structured(
            model=model,
            schema=ScreenSource,
            # A screen that reaches this ceiling truncates mid-JSON and fails
            # as a parse error, costing a full silent retry — observed at
            # exactly 8000 completion tokens on a real run. A dense screen
            # (tables, forms, modals) legitimately needs more than that.
            max_tokens=12000,
            retries=1,
            timeout=180.0,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You are the UI agent inside Stark Digital's AI Software Factory. Write ONE "
                        "screen of the application.\n\n"
                        "It is a self-contained React function component in plain JavaScript with JSX — "
                        "no imports, no exports, no build step. All screens are assembled into a single "
                        "preview file where React and ReactDOM are already global.\n\n"
                        "Every interactive element needs a real handler that does something observable; "
                        "every component you reference must be defined in this source or be one of the "
                        "other screens listed. Use in-memory mock data only — no network calls.\n\n"
                        + prompts.text("ui.visualPolish")
                        + "\n\n"
                        + skill_text
                        + learned_text
                    ),
                },
                {
                    "role": "user",
                    "content": (
                        f"Screen to write: {outline.name} at {outline.route}\n"
                        f"Purpose: {outline.purpose}\n"
                        f"It must contain: {json.dumps(outline.key_elements, indent=2)}\n\n"
                        f"Other screens in this application: {roster}\n\n"
                        f"Product objective:\n{brd['objective']}\n\n"
                        f"Data model:\n{json.dumps(brd['dataModel'], indent=2)}"
                    ),
                },
            ],
        )
        return strip_module_syntax(result.source)
    except Exception:  # noqa: BLE001 — reported as a clarification, never fatal
        log.exception("screen %s failed to generate", outline.name)
        return None
