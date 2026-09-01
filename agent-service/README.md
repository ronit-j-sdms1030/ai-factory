# Agent Service

LangGraph implementation of the pipeline's agent layer — intake through
decomposition. Built fresh against the JavaScript implementation in
`../backend` as reference, not transliterated from it.

**Status:** standalone. Nothing here is wired into the Express backend or the
frontend yet, and no agent has been run against a live model. The Express
backend in `../backend` remains the running system.

See [`../docs/architecture.md`](../docs/architecture.md) for the design and
[`../docs/agents.md`](../docs/agents.md) for per-agent specifications.

## Layout

```
app/
├── config.py      approval rules, tiers, departments, intake budget
├── state.py       typed graph state shared by every node
├── schemas.py     Pydantic models for each agent's structured output
├── llm.py         OpenRouter client with schema-validated tool calls
├── invariants.py  deterministic repairs and integrity checks
├── graph.py       StateGraph assembly; approval gates as interrupts
└── agents/
    ├── intake.py         plain request -> structured requirement
    ├── brd.py            requirement -> design, then fresh-context self-review
    ├── ui.py             design -> reviewable screens (new capability)
    └── decomposition.py  design -> dependency-ordered work items
```

## Running

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest          # 26 tests, no network required
```

The tests stub the agent nodes, so they exercise orchestration — that a gate
genuinely pauses, that state survives the pause, and that approve, reject and
revise route correctly — rather than model behaviour.

To run the graph for real, set `OPENROUTER_API_KEY` and supply a checkpointer.
`MONGODB_URI` is only needed for the Mongo checkpointer.

```python
from langgraph.checkpoint.memory import InMemorySaver
from app.graph import build_graph

graph = build_graph(checkpointer=InMemorySaver())
config = {"configurable": {"thread_id": artifact_id}}

graph.invoke({"artifact_id": artifact_id,
              "originator": {"user_id": "u1", "tier_id": "md"}}, config)
```

A checkpointer is **required** — `interrupt()` does not work without one.

## How pauses work

Each approval gate calls `interrupt()`. The graph checkpoints, stops, and
returns the pending gate to the caller. It resumes only when invoked again
with a decision:

```python
from langgraph.types import Command

graph.invoke(Command(resume={"action": "approve", "user_id": "u1"}), config)
```

`action` is `approve`, `reject`, or `revise`. Rejection ends the run and
records why; revision routes back to the agent that produced the artefact.

To inspect what a thread is waiting on:

```python
graph.get_state(config).tasks[0].interrupts
```

## Sequencing

Approval chains are ordered — MD then VP, for an MD-originated requirement —
but human approvals arrive unordered. The graph therefore owns sequencing: it
names only the current gate's tier and ignores an approval arriving early from
a later gate. `config.APPROVAL_RULES` is the source of truth and is kept
identical to `backend/src/config/hierarchy.config.js`; a divergence there would
silently change who may approve what.

## Not yet built

- FastAPI endpoints
- Integration with the Express backend and the existing workspace UI
- Any verification against a live model
- Git as the artefact store (see `../docs/architecture.md` §6)
