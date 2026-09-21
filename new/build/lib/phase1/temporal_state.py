"""Pure command/state rules shared by the Temporal workflow and unit tests."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


class CommandRefused(ValueError):
    pass


@dataclass
class WorkflowState:
    requirement_id: str
    snapshot: dict[str, Any] = field(default_factory=dict)
    revision: int = 0

    @property
    def ready(self) -> bool:
        return bool(self.snapshot)

    @property
    def awaiting(self) -> int | None:
        value = self.snapshot.get("awaiting")
        return int(value) if value is not None else None

    @property
    def terminal(self) -> bool:
        return self.snapshot.get("state") == "discarded"

    def apply(self, snapshot: dict[str, Any]) -> None:
        requirement = snapshot.get("requirement") or {}
        if requirement.get("id") != self.requirement_id:
            raise CommandRefused("activity returned a different requirement")
        self.snapshot = snapshot
        self.revision += 1

    def require_turn(self) -> None:
        if not self.ready or self.snapshot.get("phase") != "intake":
            raise CommandRefused(f"{self.requirement_id} is not accepting an intake turn")

    def require_decision(self, gate: int | None) -> int:
        if not self.ready or self.awaiting is None:
            raise CommandRefused(f"{self.requirement_id} has no open gate")
        selected = self.awaiting if gate is None else int(gate)
        if selected != self.awaiting:
            raise CommandRefused(
                f"gate {selected} is not current; awaiting gate {self.awaiting}"
            )
        return selected

    def require_brd_edit(self) -> None:
        if not self.ready or self.awaiting != 2:
            raise CommandRefused(f"{self.requirement_id} is not at Gate 2")

    def timer_seconds(self, now: datetime) -> float | None:
        due = self.snapshot.get("sla_due")
        gate = self.snapshot.get("sla_gate")
        if (
            not due
            or gate != self.awaiting
            or self.snapshot.get("escalated")
            or self.terminal
        ):
            return None
        deadline = datetime.fromisoformat(str(due).replace("Z", "+00:00"))
        return max(0.0, (deadline - now).total_seconds())
