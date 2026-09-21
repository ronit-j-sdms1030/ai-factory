"""Pure Temporal command/state tests; no server or credentials required."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from temporalio import activity
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from phase1.platform import Phase1
from phase1.service import TemporalPhase1, service_from_env
from phase1.temporal_state import CommandRefused, WorkflowState
from phase1.temporal_workflow import RequirementWorkflow


@pytest.fixture
def anyio_backend():
    return "asyncio"


def snapshot(
    requirement_id: str = "REQ-0042",
    *,
    phase: str = "intake",
    awaiting: int | None = 1,
    due: str | None = None,
    escalated: bool = False,
) -> dict:
    return {
        "requirement": {"id": requirement_id},
        "phase": phase,
        "state": "open",
        "awaiting": awaiting,
        "sla_gate": awaiting,
        "sla_due": due,
        "escalated": escalated,
    }


def test_state_rejects_cross_requirement_activity_result():
    state = WorkflowState("REQ-0042")
    with pytest.raises(CommandRefused, match="different requirement"):
        state.apply(snapshot("REQ-9999"))


def test_command_rules_follow_current_workflow_position():
    state = WorkflowState("REQ-0042")
    state.apply(snapshot(phase="awaiting_gate_1", awaiting=1))
    with pytest.raises(CommandRefused, match="intake turn"):
        state.require_turn()
    assert state.require_decision(None) == 1
    with pytest.raises(CommandRefused, match="not current"):
        state.require_decision(2)
    with pytest.raises(CommandRefused, match="Gate 2"):
        state.require_brd_edit()


def test_timer_is_owned_by_state_and_disarms_after_escalation():
    state = WorkflowState("REQ-0042")
    state.apply(
        snapshot(
            phase="awaiting_gate_1",
            due="2026-09-21T14:00:00Z",
        )
    )
    now = datetime(2026, 9, 21, 12, 0, tzinfo=timezone.utc)
    assert state.timer_seconds(now) == 7200
    state.apply(
        snapshot(
            phase="awaiting_gate_1",
            due="2026-09-21T14:00:00Z",
            escalated=True,
        )
    )
    assert state.timer_seconds(now) is None


def test_environment_selects_direct_or_temporal_without_credentials(tmp_path):
    direct = service_from_env(tmp_path / "direct", {})
    temporal = service_from_env(
        tmp_path / "temporal",
        {
            "ORCHESTRATION_MODE": "temporal",
            "TEMPORAL_ADDRESS": "temporal:7233",
        },
    )
    assert isinstance(direct, Phase1)
    assert isinstance(temporal, TemporalPhase1)
    assert temporal.address == "temporal:7233"


@pytest.mark.anyio
async def test_temporal_timer_escalates_with_time_skipping():
    class FakeActivities:
        def __init__(self):
            self.due = (
                datetime.now(timezone.utc) + timedelta(hours=1)
            ).strftime("%Y-%m-%dT%H:%M:%SZ")

        @activity.defn(name="submit_requirement")
        async def submit(self, command: dict) -> dict:
            return snapshot(
                command["requirement_id"],
                phase="awaiting_gate_1",
                due=self.due,
            )

        @activity.defn(name="escalate_sla")
        async def escalate(self, command: dict) -> dict:
            return snapshot(
                command["requirement_id"],
                phase="awaiting_gate_1",
                due=self.due,
                escalated=True,
            )

    environment = await WorkflowEnvironment.start_time_skipping()
    fake = FakeActivities()
    try:
        async with Worker(
            environment.client,
            task_queue="test-temporal-flow",
            workflows=[RequirementWorkflow],
            activities=[fake.submit, fake.escalate],
        ):
            handle = await environment.client.start_workflow(
                RequirementWorkflow.run,
                {
                    "requirement_id": "REQ-TIMER",
                    "originator": {},
                    "template": "full_governance",
                    "request_text": "test",
                },
                id="REQ-TIMER",
                task_queue="test-temporal-flow",
            )
            await handle.execute_update(RequirementWorkflow.ready)
            await environment.sleep(timedelta(hours=2))
            for _ in range(20):
                current = await handle.query(RequirementWorkflow.snapshot)
                if current["escalated"]:
                    break
                await asyncio.sleep(0)
            assert current["escalated"] is True
    finally:
        try:
            await environment.shutdown()
        except RuntimeError as exc:
            # Some restricted CI sandboxes deny Temporalite's final process signal.
            if "Permission denied" not in str(exc):
                raise


@pytest.mark.anyio
async def test_temporal_wait_state_survives_worker_restart():
    class FakeActivities:
        def __init__(self):
            self.due = (
                datetime.now(timezone.utc) + timedelta(hours=1)
            ).strftime("%Y-%m-%dT%H:%M:%SZ")

        @activity.defn(name="submit_requirement")
        async def submit(self, command: dict) -> dict:
            return snapshot(
                command["requirement_id"],
                phase="awaiting_gate_1",
                due=self.due,
            )

        @activity.defn(name="escalate_sla")
        async def escalate(self, command: dict) -> dict:
            return snapshot(
                command["requirement_id"],
                phase="awaiting_gate_1",
                due=self.due,
                escalated=True,
            )

    environment = await WorkflowEnvironment.start_time_skipping()
    fake = FakeActivities()
    worker_args = {
        "task_queue": "test-temporal-restart",
        "workflows": [RequirementWorkflow],
        "activities": [fake.submit, fake.escalate],
        "max_cached_workflows": 0,
    }
    try:
        async with Worker(environment.client, **worker_args):
            handle = await environment.client.start_workflow(
                RequirementWorkflow.run,
                {
                    "requirement_id": "REQ-RESTART",
                    "originator": {},
                    "template": "full_governance",
                    "request_text": "test",
                },
                id="REQ-RESTART",
                task_queue="test-temporal-restart",
            )
            ready = await handle.execute_update(RequirementWorkflow.ready)
            assert ready["escalated"] is False

        async with Worker(environment.client, **worker_args):
            current = await handle.query(RequirementWorkflow.snapshot)
            assert current["requirement"]["id"] == "REQ-RESTART"
            assert current["awaiting"] == 1
            assert current["sla_due"] == fake.due
    finally:
        try:
            await environment.shutdown()
        except RuntimeError as exc:
            if "Permission denied" not in str(exc):
                raise
