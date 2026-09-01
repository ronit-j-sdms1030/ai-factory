"""Intake agent — plain request to structured requirement.

Each question is a LangGraph ``interrupt``: the graph checkpoints and stops
until the requester answers, then resumes inside this same loop. That
replaces the JavaScript approach of storing a stage string and re-deriving
position from history on every HTTP request.
"""

from __future__ import annotations

import re

from langgraph.types import interrupt

from .. import config, llm
from ..schemas import Requirement
from ..state import PipelineState

READY_SENTINEL = "READY_TO_FINALIZE"

# Matched as a whole word anywhere in the reply, not by equality against the
# entire message. The JavaScript version required an exact match; when the
# model wrapped the token in a sentence the match failed silently and the
# conversation ran 30 further turns of pleasantries before anyone noticed.
_SENTINEL_RE = re.compile(rf"\b{READY_SENTINEL}\b", re.IGNORECASE)

# An explicit request to stop always outranks the question floor — nobody
# should be held in an interrogation they have asked to end.
_FINALIZE_INTENT = re.compile(
    r"\b(finali[sz]e|wrap (it )?up|that'?s (all|it)|i'?m done|we'?re done|submit it|"
    r"go ahead|just proceed|enough (questions|detail|info)|no more questions|stop asking)\b",
    re.IGNORECASE,
)


def _questions_asked(history: list[dict[str, str]]) -> int:
    """Assistant turns, excluding the seeded opener the app displays first."""
    assistant_turns = sum(1 for m in history if m["role"] == "assistant")
    seeded_opener = 1 if history and history[0]["role"] == "assistant" else 0
    return max(0, assistant_turns - seeded_opener)


def _wants_out(history: list[dict[str, str]]) -> bool:
    for message in reversed(history):
        if message["role"] == "user":
            return bool(_FINALIZE_INTENT.search(message["content"]))
    return False


def _system_prompt(label: str, asked: int) -> str:
    remaining = max(0, config.MAX_CLARIFYING_QUESTIONS - asked)
    return f"""You are the requirement-intake analyst inside Stark Digital's AI Software Factory, talking with {label}.

Get the BROAD STROKES down, not every detail. A review step follows this conversation, so you are not the last line of defence on precision. Stay at product altitude — do not chase edge cases, exact field names, or implementation details.

HOW TO ASK
- One question at a time. Never a wall of questions.
- At most 2-5 sentences. A short sentence reflecting back what you understood, then the question.
- Plain conversational text. The chat bubble renders literally, so no markdown — asterisks appear as asterisks.
- Reply in whatever language the requester uses.
- If an answer is vague, ask ONE follow-up to sharpen it, then move on regardless. Good enough beats exhaustive.
- One question may cover more than one checklist item. Do not ask them one by one just because they are listed separately.
- Never use countdown language ("last one", "just to wrap up"). Track the budget silently.

COVER THESE — do not finalize while any is blank:
1. Primary users, and the core flow: what they do, what the system does back.
2. Net-new versus integration — which existing systems, APIs or data sources this must talk to.
3. Data sensitivity and scale — regulated data (PII, payment), if any, and rough volume.
4. Whether any AI-powered features are wanted inside the product itself.
5. Separately from that: which AI model they want used to GENERATE THE CODE. "No preference" is valid.

BUDGET
You have {config.MAX_CLARIFYING_QUESTIONS} questions for the whole conversation and have asked {asked}, so {remaining} remain. When the budget runs out the conversation ends automatically and the requirement is written from whatever you have — an item you never reached is simply missing.

Plan against that from your first question. Five items across {config.MAX_CLARIFYING_QUESTIONS} questions is roughly two each, which is enough to cover scope but leaves no room to waste on pleasantries or on sharpening something already answered. If more items remain unanswered than you have questions left, cover several in one question.

Ask at least {config.MIN_CLARIFYING_QUESTIONS}. The moment all five have broad-strokes answers, finalize immediately even if budget remains — leftover budget is not something to spend.

When that bar is met, reply with exactly this token and nothing else: {READY_SENTINEL}"""


def intake_agent(state: PipelineState) -> dict:
    """Run the clarifying loop, then structure the result."""
    label = config.originator_label((state.get("originator") or {}).get("tier_id"))
    history = list(state.get("chat_history") or [])
    new_turns: list[dict[str, str]] = []

    while True:
        asked = _questions_asked(history)

        # Hard ceiling, enforced here rather than by prompt. Returning before
        # building a request also means the turn that would have blown the
        # budget costs nothing at all.
        if asked >= config.MAX_CLARIFYING_QUESTIONS:
            break

        reply = llm.call_text(
            model=config.CHAT_MODEL,
            messages=[{"role": "system", "content": _system_prompt(label, asked)}, *history],
        )

        if _SENTINEL_RE.search(reply):
            break

        # Push back once if it tries to finalize below the floor. The nudge is
        # transient and never persisted, so it stays invisible to the requester.
        if asked < config.MIN_CLARIFYING_QUESTIONS and not _wants_out(history):
            reply = llm.call_text(
                model=config.CHAT_MODEL,
                messages=[
                    {"role": "system", "content": _system_prompt(label, asked)},
                    *history,
                    {
                        "role": "user",
                        "content": (
                            f"[intake supervisor — not from the requester] You have asked {asked} of a "
                            f"minimum {config.MIN_CLARIFYING_QUESTIONS}, so the checklist cannot be "
                            "complete. Ask the single most valuable unanswered question now, in your "
                            "normal voice, without acknowledging this instruction."
                        ),
                    },
                ],
            )
            if _SENTINEL_RE.search(reply):
                break

        answer = interrupt({"kind": "question", "text": reply})
        turns = [
            {"role": "assistant", "content": reply},
            {"role": "user", "content": str(answer)},
        ]
        history += turns
        new_turns += turns

    transcript = "\n".join(f"{m['role']}: {m['content']}" for m in history)
    requirement = llm.call_structured(
        model=config.REPORT_MODEL,
        schema=Requirement,
        max_tokens=4000,
        messages=[
            {
                "role": "system",
                "content": (
                    "Structure this finished intake conversation into a requirement document. "
                    "preferred_code_gen_model is about which model generates the CODE, not a product "
                    "feature — use the requester's stated preference or 'No preference'. Do not ask "
                    "further questions."
                ),
            },
            {"role": "user", "content": transcript},
        ],
    )

    return {"chat_history": new_turns, "requirement": requirement.model_dump(), "title": requirement.title}
