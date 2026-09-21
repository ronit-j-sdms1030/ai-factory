"""Deterministic Temporal workflow for one governed requirement."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from temporalio import workflow
from temporalio.common import RetryPolicy

from phase1.temporal_state import WorkflowState

ACTIVITY_TIMEOUT = timedelta(minutes=2)
NO_DUPLICATE_RETRY = RetryPolicy(maximum_attempts=1)


@workflow.defn(name="GovernedRequirement")
class RequirementWorkflow:
    def __init__(self) -> None:
        self.state: WorkflowState | None = None

    async def _activity(self, name: str, command: dict[str, Any]) -> dict[str, Any]:
        return await workflow.execute_activity(
            name,
            command,
            start_to_close_timeout=ACTIVITY_TIMEOUT,
            retry_policy=NO_DUPLICATE_RETRY,
            result_type=dict,
        )

    @workflow.run
    async def run(self, command: dict[str, Any]) -> None:
        requirement_id = str(command["requirement_id"])
        if workflow.info().workflow_id != requirement_id:
            raise ValueError("Temporal workflow id must equal requirement id")
        self.state = WorkflowState(requirement_id)
        self.state.apply(await self._activity("submit_requirement", command))

        while not self.state.terminal:
            revision = self.state.revision
            seconds = self.state.timer_seconds(workflow.now())
            try:
                await workflow.wait_condition(
                    lambda: self.state is not None and self.state.revision != revision,
                    timeout=seconds,
                )
            except asyncio.TimeoutError:
                if self.state.revision != revision or self.state.awaiting is None:
                    continue
                result = await self._activity(
                    "escalate_sla",
                    {
                        "requirement_id": requirement_id,
                        "gate": self.state.awaiting,
                        "now": workflow.now().strftime("%Y-%m-%dT%H:%M:%SZ"),
                    },
                )
                self.state.apply(result)

    @workflow.query
    def snapshot(self) -> dict[str, Any]:
        return dict(self.state.snapshot) if self.state else {}

    @workflow.update
    async def ready(self) -> dict[str, Any]:
        await workflow.wait_condition(lambda: self.state is not None and self.state.ready)
        return dict(self.state.snapshot)

    @workflow.update
    async def user_turn(self, command: dict[str, Any]) -> dict[str, Any]:
        assert self.state is not None
        self.state.require_turn()
        command = dict(command)
        command["requirement_id"] = self.state.requirement_id
        self.state.apply(await self._activity("intake_turn", command))
        return dict(self.state.snapshot)

    @workflow.update
    async def gate_decision(self, command: dict[str, Any]) -> dict[str, Any]:
        assert self.state is not None
        gate = self.state.require_decision(command.get("gate"))
        command = dict(command)
        command.update(requirement_id=self.state.requirement_id, gate=gate)
        self.state.apply(await self._activity("gate_decision", command))
        return dict(self.state.snapshot)

    @workflow.update
    async def brd_edit(self, command: dict[str, Any]) -> dict[str, Any]:
        assert self.state is not None
        self.state.require_brd_edit()
        command = dict(command)
        command["requirement_id"] = self.state.requirement_id
        self.state.apply(await self._activity("edit_brd", command))
        return dict(self.state.snapshot)

    @workflow.update
    async def scope_edit(self, command: dict[str, Any]) -> dict[str, Any]:
        assert self.state is not None
        self.state.require_scope_edit()
        command = dict(command)
        command["requirement_id"] = self.state.requirement_id
        self.state.apply(await self._activity("edit_scope", command))
        return dict(self.state.snapshot)
