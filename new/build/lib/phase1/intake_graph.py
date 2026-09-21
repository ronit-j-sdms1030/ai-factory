"""One-activity LangGraph intake graph with no independent checkpointer."""

from __future__ import annotations

import os
from typing import Any


def run_intake_turn(platform: Any, requirement_id: str, message: str) -> dict[str, Any]:
    """Use LangGraph when installed; direct execution is the deterministic fallback."""
    if os.getenv("INTAKE_GRAPH_MODE", "langgraph").lower() == "direct":
        return platform.turn(requirement_id, message)
    try:
        from langgraph.graph import END, START, StateGraph
    except ImportError:
        return platform.turn(requirement_id, message)

    def intake_node(state: dict[str, Any]) -> dict[str, Any]:
        return {
            "result": platform.turn(
                str(state["requirement_id"]),
                str(state["message"]),
            )
        }

    builder = StateGraph(dict)
    builder.add_node("intake", intake_node)
    builder.add_edge(START, "intake")
    builder.add_edge("intake", END)
    graph = builder.compile()
    result = graph.invoke(
        {"requirement_id": requirement_id, "message": message},
    )
    return dict(result["result"])
