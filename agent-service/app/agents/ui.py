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
from concurrent.futures import ThreadPoolExecutor

from .. import config, llm
from ..schemas import ScreenSource, UIPlan
from ..state import PipelineState

log = logging.getLogger(__name__)

# Screens are independent calls, so they overlap. Capped because they share
# the provider's rate limit with whatever else the factory is running.
_MAX_CONCURRENT_SCREENS = 4

# Concrete visual expectations, because "make it nice" measurably does not
# work — the recurring failure was code that ran but looked like a wireframe.
_VISUAL_POLISH = (
    "Make these look like a real, professionally designed product rather than a wireframe: a proper "
    "layout (sidebar or top nav, content area with real spacing), a cohesive palette of two or three "
    "colours plus neutrals, readable typography with clear hierarchy, and styled interactive elements. "
    "Every number, name, date and status shown must be a specific plausible value — never a literal "
    "placeholder like '---', 'N/A' or 'TBD'. Invent realistic mock content instead."
)

# Organise around what a user does, not around who built it. Asked to
# represent several departments, models otherwise emit one nav tab per
# internal team, which reads as an org chart rather than a product.
_PRODUCT_SHAPE = (
    "Organise navigation around real user-facing workflows for this product. Never create a screen or "
    "nav item named after an internal department or team. Internal engineering concerns belong folded "
    "into a single clearly-internal area, not given equal billing beside real product features."
)


def ui_agent(state: PipelineState) -> dict:
    brd = state["brd"]

    plan = _plan_screens(brd)
    if not plan.screens:
        raise RuntimeError("the UI agent planned no screens")

    # Every screen is told what the others are called, so a cross-screen
    # reference resolves to something that exists. Without it each call is
    # blind to its siblings and invents navigation targets that were never
    # generated.
    roster = ", ".join(f"{s.name} ({s.route})" for s in plan.screens)

    with ThreadPoolExecutor(max_workers=_MAX_CONCURRENT_SCREENS) as pool:
        sources = list(pool.map(lambda s: _write_screen(brd, s, roster), plan.screens))

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

    return {"ui": {"screens": screens, "clarifications": clarifications}}


def _plan_screens(brd: dict) -> UIPlan:
    """Decide the screen set. Small output, so this call is cheap and reliable."""
    return llm.call_structured(
        model=config.DETAILED_REPORT_MODEL,
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
                    "Derive them from page_behavior and data_model.\n\n"
                    f"{_PRODUCT_SHAPE}\n\n"
                    "Where the BRD is genuinely ambiguous about interface behaviour, record a "
                    "clarification rather than guessing silently."
                ),
            },
            {
                "role": "user",
                "content": (
                    f"Objective:\n{brd['objective']}\n\n"
                    f"Pages:\n{json.dumps(brd['page_behavior'], indent=2)}\n\n"
                    f"Data model:\n{json.dumps(brd['data_model'], indent=2)}\n\n"
                    f"User flow:\n{json.dumps(brd['user_flow'], indent=2)}"
                ),
            },
        ],
    )


def _write_screen(brd: dict, outline, roster: str) -> str | None:
    """Generate one screen's source. Returns None so one failure costs one screen."""
    try:
        result = llm.call_structured(
            model=config.DETAILED_REPORT_MODEL,
            schema=ScreenSource,
            max_tokens=8000,
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
                        f"{_VISUAL_POLISH}"
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
                        f"Data model:\n{json.dumps(brd['data_model'], indent=2)}"
                    ),
                },
            ],
        )
        return result.source
    except Exception:  # noqa: BLE001 — reported as a clarification, never fatal
        log.exception("screen %s failed to generate", outline.name)
        return None
