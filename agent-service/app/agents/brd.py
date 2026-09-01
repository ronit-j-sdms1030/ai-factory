"""BRD agent — approved requirement to production-ready design, then self-review.

The critique runs as a **separate call with a fresh context**, seeing only the
requirement and the finished document, never the reasoning that produced it.
That distinction is the whole mechanism: instructing the generator to be more
careful did not measurably help, whereas reviewing the artefact cold found
four real defects on an actual BRD — including a data-model field the chosen
hardware could not physically produce, and a missing safe-state definition
for a public venue.
"""

from __future__ import annotations

import json
import logging

from .. import config, llm
from ..schemas import BRD, Critique
from ..state import PipelineState

log = logging.getLogger(__name__)

MAX_CRITIQUE_ROUNDS = 2

# Being specific is not the same as being correct, and a confidently named
# wrong component is worse than a vague one — it survives review.
_DESIGN_RIGOUR = """
DESIGN RIGOUR — work through each of these before committing to any technology:
- Fitness for THIS environment, not generic suitability. How does the component behave under this build's actual physical conditions, scale, duty cycle and real user behaviour? A part that is obvious in one setting is often wrong one setting over, and the difference is usually a property of the environment the requirement already described.
- Name the standard. If an established industry standard or published specification governs this problem domain, build on it or state explicitly why not. Reaching for a general-purpose or hobbyist-tier component where a mature domain standard exists is a design error, not a cost saving.
- Deliver what was promised. Check each capability the requirement promises is genuinely delivered, not a weaker cousin of it. If the design can only deliver a reduced version, say so in open_questions rather than quietly narrowing scope.
- No fictional precision. Every field in data_model must be something the chosen components can actually produce. Inventing a field nothing can populate makes the whole document untrustworthy.
- Failure and safety. State what happens when the system fails or loses power, and what the safe state is. Where the build touches physical systems, public spaces, money or regulated data, name the applicable safety or compliance constraint and how the design honours it.
""".strip()


def _generate(requirement: dict, transcript: str) -> BRD:
    return llm.call_structured(
        model=config.DETAILED_REPORT_MODEL,
        schema=BRD,
        max_tokens=10000,
        retries=0,
        timeout=120.0,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are the BRD analyst inside Stark Digital's AI Software Factory. Expand this "
                    "approved requirement into a document an engineering team could build from "
                    "directly. No section may be vague, generic, or read like a placeholder. Name real "
                    "entities, real pages, real security measures and real technologies.\n\n"
                    + _DESIGN_RIGOUR
                ),
            },
            {"role": "user", "content": f"Approved requirement:\n{json.dumps(requirement, indent=2)}"},
            {"role": "user", "content": f"Original intake conversation:\n{transcript}"},
        ],
    )


def _critique(requirement: dict, brd: BRD) -> Critique:
    return llm.call_structured(
        model=config.DETAILED_REPORT_MODEL,
        schema=Critique,
        max_tokens=2500,
        retries=0,
        timeout=90.0,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a senior engineer with deep domain experience, reviewing a proposed design "
                    "before it reaches a build team. You did not write it. Find where it will fail in "
                    "the real world — do not praise it and do not nitpick wording.\n\n"
                    "Judge fitness for purpose: (1) will each component work under this build's real "
                    "operating conditions — physical environment, scale, duty cycle, user behaviour? "
                    "(2) does an established standard already govern this domain that the design "
                    "ignored in favour of a general-purpose substitute? (3) is every promised "
                    "capability genuinely delivered, or has one been quietly downgraded? (4) does the "
                    "data model claim fields the chosen components cannot produce? (5) where the build "
                    "touches physical systems, public spaces, money or regulated data, is failure and "
                    "safe-state behaviour defined?\n\n"
                    "Report only defects you can tie to a concrete failure circumstance. If the design "
                    "is sound, return an empty findings list — a clean review is a valid outcome and "
                    "filler findings are worse than none."
                ),
            },
            {"role": "user", "content": f"Requirement:\n{json.dumps(requirement, indent=2)}"},
            {"role": "user", "content": f"Proposed design:\n{brd.model_dump_json(indent=2)}"},
        ],
    )


def _patch(requirement: dict, brd: BRD, findings: list) -> BRD:
    rendered = "\n".join(
        f"- [{f.severity}] {f.section}: {f.issue}\n  Fix: {f.recommendation}" for f in findings
    )
    return llm.call_structured(
        model=config.DETAILED_REPORT_MODEL,
        schema=BRD,
        max_tokens=10000,
        retries=1,  # a large payload occasionally comes back malformed; a resample usually differs
        timeout=120.0,
        messages=[
            {
                "role": "system",
                "content": (
                    "Revise this design to resolve the reviewer's findings. Return the COMPLETE "
                    "corrected document, every field, not only what changed.\n\n"
                    "Apply each finding properly rather than superficially: if a component is replaced, "
                    "update every place it appears — architecture prose, diagram, tech stack, data "
                    "model, timeline and operations must all stay consistent afterwards. A document "
                    "that swaps a component in one section and leaves the old one referenced elsewhere "
                    "is worse than the original.\n\n"
                    "Preserve everything the reviewer did not object to. Where a finding cannot be "
                    "fully resolved without information nobody has yet, make the best-supported choice "
                    "and record what still needs confirming in open_questions. Never silently drop a "
                    "finding."
                ),
            },
            {"role": "user", "content": f"Requirement:\n{json.dumps(requirement, indent=2)}"},
            {"role": "user", "content": f"Current design:\n{brd.model_dump_json(indent=2)}"},
            {"role": "user", "content": f"Findings to resolve:\n{rendered}"},
        ],
    )


def brd_agent(state: PipelineState) -> dict:
    requirement = state["requirement"]
    transcript = "\n".join(f"{m['role']}: {m['content']}" for m in state.get("chat_history") or [])

    brd = _generate(requirement, transcript)

    rounds = 0
    previous_count = float("inf")
    for _ in range(MAX_CRITIQUE_ROUNDS):
        try:
            critique = _critique(requirement, brd)
        except RuntimeError as exc:
            log.warning("critique failed, keeping current BRD: %s", exc)
            break

        actionable = [f for f in critique.findings if f.severity in ("high", "medium")]
        if not actionable:
            break

        # Plateau guard: a reviewer that keeps restating the same objection
        # must not loop. Surface the findings instead of discarding them.
        if len(actionable) >= previous_count:
            brd.open_questions += [_unresolved(f) for f in actionable]
            break

        previous_count = len(actionable)
        rounds += 1
        try:
            brd = _patch(requirement, brd, actionable)
        except RuntimeError as exc:
            # A failed rewrite must degrade to "flagged for a human", never to
            # silence — the findings are real and already paid for.
            log.warning("patch failed, surfacing findings instead: %s", exc)
            brd.open_questions += [_unresolved(f) for f in actionable]
            break

    return {"brd": brd.model_dump(), "critique_rounds": rounds}


def _unresolved(finding) -> str:
    return (
        f"Unresolved design review finding ({finding.severity}, {finding.section}): "
        f"{finding.issue} Suggested fix: {finding.recommendation}"
    )
