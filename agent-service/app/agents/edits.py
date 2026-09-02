"""Conversational editing of a BRD and of department packages.

Small path-based replacements rather than a whole-document rewrite: a bad edit
can then only damage the field it names, and the diff is reviewable. The
alternative — asking for the full document back — risks silent collateral
change in sections nobody discussed.
"""

from __future__ import annotations

import json
import logging

from .. import config, llm
from ..schemas import FsdEdit, LearnedRule, ScreenEditResult, TeamReportEdit

log = logging.getLogger(__name__)


def run_fsd_chat_edit(
    *,
    originator_label: str,
    actor_role: str,
    title: str,
    requirement: dict,
    detailed_report: dict | None,
    history: list[dict[str, str]],
) -> FsdEdit:
    """Apply a reviewer's requested change to the requirement or its BRD."""
    return llm.call_structured(
        model=config.REPORT_MODEL,
        schema=FsdEdit,
        max_tokens=5000,
        retries=2,  # path-based edits are fiddly; a resample often fixes a bad path
        messages=[
            {
                "role": "system",
                "content": (
                    f"You are editing a requirement and its BRD inside Stark Digital's AI Software "
                    f"Factory. The requirement came from {originator_label}. You are speaking with "
                    f"{actor_role}, who is asking for a change.\n\n"
                    "Return the smallest set of path-based replacements that satisfies the request. "
                    "Change only what was asked for — never rewrite sections nobody mentioned, and "
                    "never reformat untouched text.\n\n"
                    "target is 'title', 'requirement', or 'detailedReport'. path is a dot path within "
                    "that target and must reference a field that already exists; an empty path "
                    "replaces the whole target. A title edit needs an empty path and a non-empty "
                    "string.\n\n"
                    "If the request is unclear or would need information nobody has, say so in "
                    "change_summary and return the single closest safe edit rather than guessing "
                    "broadly."
                ),
            },
            {"role": "user", "content": f"Current title:\n{title}"},
            {"role": "user", "content": f"Current requirement:\n{json.dumps(requirement, indent=2, default=str)}"},
            {
                "role": "user",
                "content": f"Current BRD:\n{json.dumps(detailed_report or {}, indent=2, default=str)}",
            },
            *history,
        ],
    )


def run_team_report_chat_edit(*, department: str, package: dict, message: str) -> TeamReportEdit:
    """Apply a team lead's requested change to their own department package."""
    return llm.call_structured(
        model=config.REPORT_MODEL,
        schema=TeamReportEdit,
        max_tokens=6000,
        retries=1,
        messages=[
            {
                "role": "system",
                "content": (
                    f"You are revising the {department} department's work package inside Stark "
                    "Digital's AI Software Factory, at the request of that department's team lead.\n\n"
                    f"Return the COMPLETE revised package. The team field must remain exactly "
                    f"'{department}' — reassigning work to another department is not a change a team "
                    "lead can make here.\n\n"
                    "Preserve everything the request did not ask about. In particular keep the data "
                    "model's entity and field names spelled exactly as they are: other departments "
                    "generate code against those same names, and a rename here produces modules that "
                    "cannot be combined."
                ),
            },
            {"role": "user", "content": f"Current package:\n{json.dumps(package, indent=2, default=str)}"},
            {"role": "user", "content": f"Requested change:\n{message}"},
        ],
    )


def _skill_section() -> str:
    """The design system, so a revision does not drift off the house style.

    A screen edited without it comes back correct on the requested change and
    quietly wrong everywhere else — which is the harder defect to spot, since
    the reviewer is looking at the thing they asked for.
    """
    try:
        from ..design_system import prompt_section

        return prompt_section()
    except Exception as exc:  # noqa: BLE001 — an edit is worth more than its styling
        log.warning("could not load the design-system skill file for an edit: %s", exc)
        return ""


def run_ui_screen_edit(*, screen: dict, instruction: str, roster: str, objective: str) -> ScreenEditResult:
    """Rewrite one screen to satisfy a plain-language request.

    A whole-file rewrite rather than the path-based edits the BRD and package
    editors use: those documents are trees of named fields, where a path
    identifies exactly what to change. A screen is one string of source, so
    there is nothing to address but the whole of it.

    Scoped to a single screen deliberately. Regenerating the design is the
    blunt alternative and produces a different set of screens rather than the
    same set with one changed, so a reviewer fixing one thing would gamble
    every screen that was already right.
    """
    return llm.call_structured(
        model=config.UI_MODEL,
        schema=ScreenEditResult,
        max_tokens=12000,
        retries=1,
        timeout=180.0,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are the UI agent inside Stark Digital's AI Software Factory, revising ONE "
                    "screen a reviewer has asked you to change.\n\n"
                    "Return the complete revised component. It stays a self-contained React function "
                    "component in plain JavaScript with JSX — no imports, no exports, no build step — "
                    "keeping the SAME component name, because the preview looks it up by name.\n\n"
                    "Change what was asked for and leave the rest alone. A reviewer asking for a "
                    "column to be added has not asked for the styling to be reworked, and a screen "
                    "that comes back subtly different everywhere cannot be reviewed.\n\n"
                    + _skill_section()
                ),
            },
            {"role": "user", "content": f"Product objective:\n{objective}"},
            {"role": "user", "content": f"Other screens in this application: {roster}"},
            {"role": "user", "content": f"Current source of {screen.get('name')}:\n{screen.get('source')}"},
            {"role": "user", "content": f"The change requested:\n{instruction}"},
        ],
    )


def extract_lesson(
    *, before: str, after: str, instruction: str = "", active_rules: list[str] | None = None
) -> LearnedRule:
    """Decide whether a correction is a house rule or a one-off.

    Most edits are one-offs, and the prompt is the wrong place for those: a
    rule invented from a single content change is applied to every screen
    afterwards and is harder to notice than the correction it came from. The
    schema pushes toward false on doubt for that reason.

    ``active_rules`` are shown to the model so it can flag a conflict rather
    than propose a rival silently. Without them, two reviewers with opposite
    date-format preferences would each look right in isolation and the second
    would simply out-vote the first the next time it repeats — nothing would
    ever say the two disagree.

    Runs on the report model rather than the UI model. Judging whether a
    change generalises is a reading task, not a coding one, and the coder
    models in this pipeline have been the weakest at following a schema.
    """
    return llm.call_structured(
        model=config.REPORT_MODEL,
        schema=LearnedRule,
        max_tokens=1200,
        retries=1,
        timeout=60.0,
        messages=[
            {
                "role": "system",
                "content": (
                    "A reviewer corrected a generated screen inside Stark Digital's AI Software "
                    "Factory. Decide whether that correction should become a standing instruction "
                    "for every screen generated afterwards, or whether it applied only here.\n\n"
                    "Generalise sparingly. Formatting, wording and layout conventions are house "
                    "standards worth learning. A different heading, a different mock value or a "
                    "field only this screen needs are not, however tempting the pattern looks from "
                    "one example.\n\n"
                    "If a rule set is supplied below, check whether the new rule disagrees with one "
                    "of them — e.g. it picks a different date format, or the opposite alignment. "
                    "Report that in `contradicts` rather than silently proposing a rival."
                ),
            },
            *(
                [
                    {
                        "role": "user",
                        "content": "ACTIVE RULES already in effect:\n"
                        + "\n".join(f"- {r}" for r in active_rules),
                    }
                ]
                if active_rules
                else []
            ),
            *(
                [{"role": "user", "content": f"The reviewer asked for:\n{instruction}"}]
                if instruction else []
            ),
            {"role": "user", "content": f"Before:\n{before[:6000]}"},
            {"role": "user", "content": f"After:\n{after[:6000]}"},
        ],
    )
