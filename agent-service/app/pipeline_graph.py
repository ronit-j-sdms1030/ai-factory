"""The pipeline, as a real compiled LangGraph. All four agents run here.

For a long time this project had a ``graph.py`` that declared the whole
pipeline and executed none of it: ``build_graph`` was referenced only from
``langgraph.json``, so LangGraph Studio drew it while a hand-written if/elif
chain in the routes decided what actually happened next. A framework that is
imported but never invoked satisfies neither SoW 5.0's "agent orchestration
framework" nor anyone reading the dependency list. That file is gone.

This module is the part that genuinely runs. It owns **which agent runs next
and what state carries between them** — checkpointed to Mongo so a gate is a
real pause rather than a condition re-derived on the next request::

    intake ⏸(loop) → finalize → gate_requirement ⏸
      → brd → gate_brd ⏸ → ui → gate_ui ⏸ → workitems → END

**Intake is a loop, and the interrupt comes first.** A resumed node re-runs
from the top, so a model call placed above ``interrupt()`` would fire twice
per turn — once before the pause and again on resume, billing double and
appending the reply twice. Taking the message first means everything after it
runs exactly once per turn.

**What it deliberately does not own.** Approvals, stage transitions and who
may act are the state machine's; git publishing and artifact persistence are
the route's. That division is not a compromise, it is the one in
``architecture.md`` §5: git operations and human gates are explicitly not
agents and must stay deterministic. The graph decides *what generates*; the
state machine decides *what is permitted*.

**Why the FSD loop is absent.** The real workflow has eight stages and a
client review loop (``fsd_review`` → ``fsd_pending_client`` →
``fsd_final_approval``) that the old ``graph.py`` never modelled. Rather than
model it badly, the gates here are opaque: the graph pauses and the state
machine — which does model it — decides when to resume. The graph never needs
to know which of the eight stages the pause corresponds to.

**The transcript is not duplicated.** The intake node reads the conversation
from the artifact rather than accumulating it in graph state, and the route
writes each message back. Holding it in both places would put the same data
under two authorities that can disagree — and the workspace renders from
Mongo, so a checkpoint that drifted would make the UI lie about what was
said. The checkpoint holds position, not content.

**Thread identity is the artifact.** ``thread_id`` is the artifact id, so a
requirement's generation history is one resumable thread. A thread whose
checkpoint is missing (an artifact predating this module, or a dropped
checkpoint collection) is rehydrated from the artifact itself — the artifact
is authoritative, the checkpoint is an accelerator.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, Callable, TypedDict

from langgraph.checkpoint.mongodb import MongoDBSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from bson import ObjectId

from . import db
from .agents.brd import brd_agent
from .agents.decomposition import decomposition_agent
from .agents.intake import finalize_requirement, run_chat_turn
from .agents.ui import ui_agent

log = logging.getLogger(__name__)

# The three artefacts the generation half produces, in order. Also the resume
# points: naming a phase is how the route says "generate this next".
PHASES = ("brd", "ui", "workitems")


def _last(existing: Any, incoming: Any) -> Any:
    """Last write wins. Nodes here replace an artefact rather than merge it."""
    return incoming if incoming is not None else existing


class GenerationState(TypedDict, total=False):
    """What travels between the generation agents.

    Deliberately smaller than the artifact document. The artifact carries 33
    fields including edit histories, discussion threads and pull request
    records; none of that is an input to generating the next artefact, and
    copying it in would create a second authority for data Mongo already owns.
    """

    # ── identity, so a node can read the conversation it is continuing ───────
    artifact_id: str
    originator_label: str

    # ── inputs, supplied by the route from the artifact ──────────────────────
    requirement: dict[str, Any]
    chat_history: list[dict[str, str]]
    models: dict[str, str]
    title: str

    # The latest intake turn — a question for the requester, or the signal
    # that the conversation is complete. Read by the route, not by a node.
    turn: Annotated[dict[str, Any], _last]

    # ── produced by the nodes ────────────────────────────────────────────────
    brd: Annotated[dict[str, Any], _last]
    brd_provenance: Annotated[dict[str, Any], _last]
    ui: Annotated[dict[str, Any], _last]
    work_items: Annotated[dict[str, Any], _last]
    critique_rounds: int


def _history_of(artifact_id: str) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """The artifact and its transcript, read fresh from Mongo.

    Read rather than carried in graph state so there is exactly one copy of
    the conversation. The route has already written the incoming message by
    the time a node runs, so this sees it.
    """
    artifact = db.artifacts().find_one({"_id": ObjectId(artifact_id)}) or {}
    history = [
        {"role": m["role"], "content": m["content"]}
        for m in artifact.get("chatHistory") or []
    ]
    return artifact, history


def _intake_node(state: GenerationState) -> Command:
    """One clarifying turn: take the requester's message, answer or finish.

    ``interrupt`` is the first statement deliberately — see the module
    docstring. Everything below it runs once per message; anything above it
    would run again on every resume.
    """
    interrupt({"awaiting": "the requester's next message"})

    _, history = _history_of(state["artifact_id"])
    turn = run_chat_turn(history, state.get("originator_label") or "Client")

    if turn["type"] == "reply":
        # Back to this same node, which interrupts again for the next message.
        return Command(goto="intake", update={"turn": turn})
    return Command(goto="finalize", update={"turn": turn})


def _finalize_node(state: GenerationState) -> dict:
    """Structure the finished conversation into a requirement document."""
    _, history = _history_of(state["artifact_id"])
    requirement = finalize_requirement(history)
    return {
        "requirement": requirement.model_dump(by_alias=True),
        "title": requirement.title,
        "turn": {"type": "ready"},
    }


def _brd_node(state: GenerationState) -> dict:
    result = brd_agent(state)
    return {"brd": result["brd"], "brd_provenance": result.get("brd_provenance") or {}}


def _ui_node(state: GenerationState) -> dict:
    return {"ui": ui_agent(state)["ui"]}


def _workitems_node(state: GenerationState) -> dict:
    return {"work_items": decomposition_agent(state)["work_items"]}


def _after_requirement(state: GenerationState) -> Command:
    """Pause until the requirement has been submitted and cleared gate 0."""
    interrupt({"gate": "GATE_0_REQUIREMENT", "produced": "requirement"})
    return Command(goto="brd")


def _after_brd(state: GenerationState) -> Command:
    """Pause until the BRD has cleared its approval chain.

    The payload is informational — the state machine has already decided this
    is permitted before resuming, and re-deciding it here would be a second
    implementation of the approval rules that could disagree with the first.
    """
    interrupt({"gate": "GATE_1_BRD", "produced": "brd"})
    return Command(goto="ui")


def _after_ui(state: GenerationState) -> Command:
    interrupt({"gate": "GATE_2_UI", "produced": "ui"})
    return Command(goto="workitems")


def build_pipeline_graph(checkpointer=None):
    """Compile the pipeline. Without a checkpointer the gates cannot pause."""
    builder = StateGraph(GenerationState)

    # Gates and the intake loop route with Command(goto=...), which LangGraph
    # cannot infer from a function body — without `destinations` the compiled
    # graph claims they lead nowhere and orphans everything downstream.
    builder.add_node("intake", _intake_node, destinations=("intake", "finalize"))
    builder.add_node("finalize", _finalize_node)
    builder.add_node("gate_requirement", _after_requirement, destinations=("brd",))
    builder.add_node("brd", _brd_node)
    builder.add_node("gate_brd", _after_brd, destinations=("ui",))
    builder.add_node("ui", _ui_node)
    builder.add_node("gate_ui", _after_ui, destinations=("workitems",))
    builder.add_node("workitems", _workitems_node)

    builder.add_edge(START, "intake")
    builder.add_edge("finalize", "gate_requirement")
    builder.add_edge("brd", "gate_brd")
    builder.add_edge("ui", "gate_ui")
    builder.add_edge("workitems", END)

    return builder.compile(checkpointer=checkpointer)



_graph = None


def graph():
    """The compiled graph, checkpointed to Mongo where that is possible.

    Built once and reused: compilation is cheap but the checkpointer holds a
    Mongo client, and a fresh one per generation would leak connections on a
    service that generates continuously.

    **A checkpointer that cannot be built must not stop generation.**
    ``MongoDBSaver`` creates its indexes on construction, so it needs
    privileges beyond the reads and writes the rest of the service uses — and
    before this module existed, generating a BRD did not depend on a
    checkpointer at all. Failing here would turn a missing optimisation into a
    total outage of the pipeline's main job. Without one the gates cannot
    pause, so every phase takes the detached path: exactly the behaviour this
    module replaced, which is a safe floor rather than a broken state.
    """
    global _graph
    if _graph is None:
        try:
            saver = MongoDBSaver(
                db.client(),
                db_name=db.db().name,
                checkpoint_collection_name="graphCheckpoints",
                writes_collection_name="graphCheckpointWrites",
            )
        except Exception as exc:  # noqa: BLE001 — generation matters more than resumability
            log.warning(
                "no checkpointer — generation will run unresumable, one phase at a time: %s", exc
            )
            saver = None
        _graph = build_pipeline_graph(checkpointer=saver)
    return _graph


def _config(artifact_id: str) -> dict:
    return {"configurable": {"thread_id": str(artifact_id)}}


def _inputs(artifact: dict[str, Any]) -> GenerationState:
    """The artifact reduced to what the generation agents actually read."""
    return {
        "requirement": artifact.get("content") or {},
        "chat_history": [
            {"role": m["role"], "content": m["content"]}
            for m in artifact.get("chatHistory") or []
        ],
        "models": artifact.get("modelOverrides") or {},
        "title": artifact.get("title") or "Untitled",
    }


def _position(artifact: dict[str, Any]) -> str | None:
    """Which phase this artifact is waiting to have generated, from Mongo.

    The artifact is authoritative rather than the checkpoint. A checkpoint can
    be absent (an artifact predating this module) or stale (restored data),
    and generating a second BRD over an approved one because a checkpoint said
    so would be a far worse failure than recomputing a resume point.
    """
    if not artifact.get("detailedReport"):
        return "brd"
    if not artifact.get("ui"):
        return "ui"
    if not artifact.get("teamReports"):
        return "workitems"
    return None


# A paused thread reports the *gate* it stopped inside, not the node that gate
# leads to — ``state.next`` is ``('gate_brd',)`` while waiting to produce the
# UI. Comparing that to the phase name directly never matches, which would
# send every generation down the detached path and leave the graph as
# decorative as the one this replaces.
_GATE_UNBLOCKS = {"gate_requirement": "brd", "gate_brd": "ui", "gate_ui": "workitems"}


def advance(artifact: dict[str, Any], phase: str) -> dict[str, Any]:
    """Run exactly one generation phase and return what it produced.

    Resumes the thread when the graph is parked at the gate immediately before
    this phase; otherwise runs the node directly.
    """
    compiled = graph()
    config = _config(artifact["_id"])
    try:
        state = compiled.get_state(config)
    except ValueError:
        # Compiled without a checkpointer (see ``graph``) — there is no thread
        # to inspect or resume, so every phase runs detached.
        return _run_detached(artifact, phase)
    parked_at = state.next[0] if state.next else None

    if parked_at and _GATE_UNBLOCKS.get(parked_at) == phase:
        # Parked at the gate immediately before this phase: resume through it.
        result = compiled.invoke(Command(resume={"action": "approve"}), config)
    else:
        # There is deliberately no fresh-start branch: the graph begins at
        # intake, so starting a thread here would open a conversation rather
        # than generate. A requirement that reaches this point without a
        # usable checkpoint — created before intake joined the graph, or
        # whose checkpoint was lost — runs the node directly against the
        # artifact, which is authoritative. Logged because a healthy pipeline
        # resumes instead.
        log.info(
            "thread for %s not parked before %s (at %s) — running the phase directly",
            artifact.get("_id"), phase, parked_at,
        )
        result = _run_detached(artifact, phase)

    return result


def intake_turn(artifact: dict[str, Any], label: str) -> dict[str, Any]:
    """Advance the conversation one turn, on the graph.

    Returns the agent's ``turn`` — ``{"type": "reply", "text": ...}`` for a
    question, ``{"type": "ready"}`` once the conversation is complete — and,
    when complete, the structured ``requirement`` alongside it.

    The caller must have written the incoming message to the artifact first:
    the node reads the transcript from Mongo, which is what keeps a single
    copy of it.
    """
    compiled = graph()
    config = _config(artifact["_id"])
    try:
        state = compiled.get_state(config)
    except ValueError:
        return _intake_detached(artifact, label)

    parked_at = state.next[0] if state.next else None

    if parked_at and parked_at != "intake":
        # The conversation is over and the thread has moved on to a gate.
        # Resuming here would drive the graph *through* that gate — observed
        # doing exactly that: a fourth message after finalisation resumed
        # `gate_requirement` and began generating the BRD, with no approval
        # recorded. The route's stage check would normally prevent a call
        # getting this far, but a gate that depends on a caller's guard is
        # the transitive kind this project has been removing.
        log.info("intake asked to continue past the conversation (parked at %s)", parked_at)
        return {"turn": {"type": "ready"}, "requirement": state.values.get("requirement")}

    if not parked_at:
        # Thread not started: run to the first interrupt so there is a pause
        # to resume into. The node does nothing before that point, so this
        # costs no model call.
        compiled.invoke(
            {"artifact_id": str(artifact["_id"]), "originator_label": label},
            config,
        )

    result = compiled.invoke(Command(resume="message"), config)
    return {"turn": result.get("turn") or {}, "requirement": result.get("requirement")}


def _intake_detached(artifact: dict[str, Any], label: str) -> dict[str, Any]:
    """One turn without a checkpointer — same agents, no resumability."""
    _, history = _history_of(str(artifact["_id"]))
    turn = run_chat_turn(history, label)
    if turn["type"] == "reply":
        return {"turn": turn, "requirement": None}
    requirement = finalize_requirement(history)
    return {"turn": {"type": "ready"}, "requirement": requirement.model_dump(by_alias=True)}


def regenerate(artifact: dict[str, Any], phase: str) -> dict[str, Any]:
    """Re-run one phase deliberately, outside the sequence.

    A reviewer asking for a fresh BRD is not resuming the thread — the thread
    has already moved past that gate, and resuming would produce the *next*
    artefact rather than a new copy of this one. So this runs the node
    directly, against the artifact as it stands.
    """
    return _run_detached(artifact, phase)


def _run_detached(artifact: dict[str, Any], phase: str) -> dict[str, Any]:
    """Run one node outside the thread, for an artifact with no live checkpoint.

    Uses the same node functions, so behaviour cannot drift from the graph
    path — what is skipped is the checkpointing, not the work.
    """
    state: GenerationState = {
        **_inputs(artifact),
        "brd": artifact.get("detailedReport") or {},
        "ui": artifact.get("ui") or {},
    }
    node = {"brd": _brd_node, "ui": _ui_node, "workitems": _workitems_node}[phase]
    return node(state)
