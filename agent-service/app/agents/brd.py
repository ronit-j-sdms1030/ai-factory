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

from langsmith import traceable

from .. import config, llm
from . import model_for
from ..schemas import BRD, Critique
from ..state import PipelineState
from .. import prompts

log = logging.getLogger(__name__)

MAX_CRITIQUE_ROUNDS = 2

# Being specific is not the same as being correct, and a confidently named
# wrong component is worse than a vague one — it survives review.
def _generate(requirement: dict, transcript: str, model: str) -> BRD:
    return llm.call_structured(
        model=model,
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
                    + prompts.text("brd.rigour")
                ),
            },
            {"role": "user", "content": f"Approved requirement:\n{json.dumps(requirement, indent=2)}"},
            {"role": "user", "content": f"Original intake conversation:\n{transcript}"},
        ],
    )


def _critique(requirement: dict, brd: BRD, model: str) -> Critique:
    return llm.call_structured(
        model=model,
        schema=Critique,
        max_tokens=2500,
        retries=0,
        timeout=90.0,
        messages=[
            {
                "role": "system",
                "content": prompts.text("brd.critique"),
            },
            {"role": "user", "content": f"Requirement:\n{json.dumps(requirement, indent=2)}"},
            {"role": "user", "content": f"Proposed design:\n{brd.model_dump_json(indent=2)}"},
        ],
    )


def _patch(requirement: dict, brd: BRD, findings: list, model: str) -> BRD:
    rendered = "\n".join(
        f"- [{f.severity}] {f.section}: {f.issue}\n  Fix: {f.recommendation}" for f in findings
    )
    return llm.call_structured(
        model=model,
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
                    "and record what still needs confirming in openQuestions. Never silently drop a "
                    "finding."
                ),
            },
            {"role": "user", "content": f"Requirement:\n{json.dumps(requirement, indent=2)}"},
            {"role": "user", "content": f"Current design:\n{brd.model_dump_json(indent=2)}"},
            {"role": "user", "content": f"Findings to resolve:\n{rendered}"},
        ],
    )


# The finalised requirement already distills the conversation's substance, so
# the transcript is supporting context rather than the primary source. An
# unusually long intake should not blow up prompt size and generation time in
# proportion to it.
MAX_HISTORY_MESSAGES = 30


def cap_history(history: list[dict], maximum: int = MAX_HISTORY_MESSAGES) -> list[dict]:
    """Keep the opening and closing messages, drop the middle.

    Trimming from one end loses something either way: the start sets the
    original framing, and the end holds the refined final answers. The middle
    is where the conversation is most redundant, so that is what goes.

    The gap is marked rather than closed silently — the same rule
    ``buildFilesContext`` follows when it drops files from a review. Splicing
    two halves together invisibly hands the model a transcript that reads as
    continuous when it is not.
    """
    if len(history) <= maximum:
        return history
    keep_start = maximum // 5
    keep_end = maximum - keep_start
    omitted = len(history) - maximum
    return [
        *history[:keep_start],
        {"role": "user", "content": f"({omitted} earlier messages omitted for length.)"},
        *history[len(history) - keep_end:],
    ]


@traceable(name="BRD Agent")
def brd_agent(state: PipelineState) -> dict:
    requirement = state["requirement"]
    transcript = "\n".join(
        f"{m['role']}: {m['content']}" for m in cap_history(state.get("chat_history") or [])
    )

    model = model_for(state, "brd")
    brd = _generate(requirement, transcript, model)

    rounds = 0
    previous_count = float("inf")
    for _ in range(MAX_CRITIQUE_ROUNDS):
        try:
            critique = _critique(requirement, brd, model)
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
            brd = _patch(requirement, brd, actionable, model)
        except RuntimeError as exc:
            # A failed rewrite must degrade to "flagged for a human", never to
            # silence — the findings are real and already paid for.
            log.warning("patch failed, surfacing findings instead: %s", exc)
            brd.open_questions += [_unresolved(f) for f in actionable]
            break

    return {
        "brd": brd.model_dump(by_alias=True),
        "critique_rounds": rounds,
        # SoW 7.0 audit fields: which model and which instructions produced
        # this document. Recorded here rather than by the route so they cannot
        # drift from what actually ran.
        "brd_provenance": {"model": model, "promptVersions": prompts.versions("brd")},
    }


def _unresolved(finding) -> str:
    return (
        f"Unresolved design review finding ({finding.severity}, {finding.section}): "
        f"{finding.issue} Suggested fix: {finding.recommendation}"
    )
