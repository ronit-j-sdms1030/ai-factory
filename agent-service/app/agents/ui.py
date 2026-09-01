"""UI agent — approved BRD to reviewable screens, before any code exists.

New capability, not a port. The JavaScript pipeline has no equivalent: its
nearest artefact is the project-demo synthesis, which runs *after* all code is
generated, so the thing meant to gate code generation is produced by it.
SoW 12.0 requires the opposite — "code generation for the affected scope
cannot start until UI approval is recorded".

Clarifications are a first-class output rather than something the model
resolves silently, per SoW 11.0: "The agent raises clarification requests
where the BRD is ambiguous."
"""

from __future__ import annotations

import json

from .. import config, llm
from ..schemas import UIDesign
from ..state import PipelineState

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

    design = llm.call_structured(
        model=config.DETAILED_REPORT_MODEL,
        schema=UIDesign,
        max_tokens=14000,
        retries=1,
        timeout=180.0,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are the UI agent inside Stark Digital's AI Software Factory. Generate the "
                    "application interface from this approved BRD, before any backend code exists.\n\n"
                    "Each screen is a self-contained React function component in plain JavaScript with "
                    "JSX — no imports, no exports, no build step. They are assembled into a single "
                    "preview file where React and ReactDOM are already global.\n\n"
                    "Derive the screens from page_behavior and data_model. Every interactive element "
                    "needs a real handler that does something observable; every component you reference "
                    "must be defined in that screen's source or be another screen in this set. Use "
                    "in-memory mock data only — no network calls.\n\n"
                    f"{_PRODUCT_SHAPE}\n\n{_VISUAL_POLISH}\n\n"
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

    return {"ui": design.model_dump()}
