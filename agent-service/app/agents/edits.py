"""Conversational editing of a BRD and of department packages.

Small path-based replacements rather than a whole-document rewrite: a bad edit
can then only damage the field it names, and the diff is reviewable. The
alternative — asking for the full document back — risks silent collateral
change in sections nobody discussed.
"""

from __future__ import annotations

import json

from .. import config, llm
from ..schemas import FsdEdit, TeamReportEdit


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
