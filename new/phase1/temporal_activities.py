"""Temporal activities: every adapter, Git, model, and store side effect lives here."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from temporalio import activity

from phase1.intake_graph import run_intake_turn
from phase1.platform import Phase1


class RequirementActivities:
    def __init__(self, root: Path):
        self.root = Path(root)

    def _platform(self) -> Phase1:
        return Phase1(self.root)

    @activity.defn(name="submit_requirement")
    def submit(self, command: dict[str, Any]) -> dict[str, Any]:
        return self._platform().submit(
            dict(command["originator"]),
            str(command["template"]),
            str(command["request_text"]),
            requirement_id=str(command["requirement_id"]),
        )

    @activity.defn(name="intake_turn")
    def turn(self, command: dict[str, Any]) -> dict[str, Any]:
        return run_intake_turn(
            self._platform(),
            str(command["requirement_id"]),
            str(command["message"]),
        )

    @activity.defn(name="gate_decision")
    def decide(self, command: dict[str, Any]) -> dict[str, Any]:
        return self._platform().decide(
            str(command["requirement_id"]),
            dict(command["actor"]),
            str(command["outcome"]),
            gate=int(command["gate"]),
            channel=str(command.get("channel") or "workspace"),
            reason=str(command.get("reason") or ""),
            revise_side=str(command.get("revise_side") or ""),
        )

    @activity.defn(name="edit_brd")
    def edit_brd(self, command: dict[str, Any]) -> dict[str, Any]:
        return self._platform().edit_brd(
            str(command["requirement_id"]),
            dict(command["editor"]),
            str(command["content"]),
        )

    @activity.defn(name="edit_scope")
    def edit_scope(self, command: dict[str, Any]) -> dict[str, Any]:
        return self._platform().edit_scope(
            str(command["requirement_id"]),
            dict(command["editor"]),
            str(command.get("title") or ""),
            dict(command["report"]),
        )

    @activity.defn(name="connector_call")
    def connector_call(self, command: dict[str, Any]) -> dict[str, Any]:
        import connectors

        return connectors.call(
            str(command["role"]),
            str(command["action"]),
            dict(command.get("payload") or {}),
            client_id=str(command["client_id"]),
            actor=dict(command.get("actor") or {}),
            write=bool(command.get("write")),
        )

    @activity.defn(name="reload_requirement")
    def reload_requirement(self, command: dict[str, Any]) -> dict[str, Any]:
        return self._platform().get(str(command["requirement_id"]))

    @activity.defn(name="escalate_sla")
    def escalate(self, command: dict[str, Any]) -> dict[str, Any]:
        platform = self._platform()
        platform.escalate(
            str(command["requirement_id"]),
            gate=int(command["gate"]),
            now=str(command["now"]),
        )
        return platform.get(str(command["requirement_id"]))
