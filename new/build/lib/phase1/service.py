"""Environment-selected direct or Temporal facade with the existing Phase1 API."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any, Mapping

from temporalio.client import Client

from phase1 import webhook
from phase1.adapters import RuntimeAdapters
from phase1.platform import Phase1


class TemporalPhase1:
    def __init__(
        self,
        root: Path,
        *,
        address: str,
        namespace: str = "default",
        task_queue: str = "governed-requirements",
    ):
        self.root = Path(root)
        self.address = address
        self.namespace = namespace
        self.task_queue = task_queue
        self.webhook_secret = os.getenv("WEBHOOK_SECRET", "dev-webhook-secret").encode()

    def _run(self, awaitable):
        return asyncio.run(awaitable)

    async def _client(self) -> Client:
        return await Client.connect(self.address, namespace=self.namespace)

    def _direct(self) -> Phase1:
        return Phase1(self.root, webhook_secret=self.webhook_secret)

    def submit(
        self,
        originator: dict[str, Any],
        template: str,
        request_text: str,
        *,
        requirement_id: str | None = None,
    ) -> dict[str, Any]:
        rid = requirement_id or RuntimeAdapters.from_env(self.root).store.next_id()

        async def start() -> dict[str, Any]:
            client = await self._client()
            handle = await client.start_workflow(
                "GovernedRequirement",
                {
                    "requirement_id": rid,
                    "originator": originator,
                    "template": template,
                    "request_text": request_text,
                },
                id=rid,
                task_queue=self.task_queue,
            )
            return await handle.execute_update("ready")

        return self._run(start())

    def get(self, requirement_id: str) -> dict[str, Any]:
        async def query() -> dict[str, Any]:
            client = await self._client()
            return await client.get_workflow_handle(requirement_id).query("snapshot")

        return self._run(query())

    def turn(
        self,
        requirement_id: str,
        message: str,
        *,
        as_originator: bool = False,
    ) -> dict[str, Any]:
        if as_originator:
            raise ValueError("as_originator is reserved for the submit activity")
        return self._update(requirement_id, "user_turn", {"message": message})

    def decide(
        self,
        requirement_id: str,
        actor: dict[str, Any],
        outcome: str,
        *,
        gate: int | None = None,
        channel: str = "workspace",
    ) -> dict[str, Any]:
        return self._update(
            requirement_id,
            "gate_decision",
            {"actor": actor, "outcome": outcome, "gate": gate, "channel": channel},
        )

    def edit_brd(
        self,
        requirement_id: str,
        editor: dict[str, Any],
        content: str,
    ) -> dict[str, Any]:
        return self._update(
            requirement_id,
            "brd_edit",
            {"editor": editor, "content": content},
        )

    def _update(
        self,
        requirement_id: str,
        name: str,
        command: dict[str, Any],
    ) -> dict[str, Any]:
        async def update() -> dict[str, Any]:
            client = await self._client()
            handle = client.get_workflow_handle(requirement_id)
            return await handle.execute_update(name, command)

        return self._run(update())

    def ingest_webhook(self, raw: bytes, signature_header: str) -> dict[str, Any]:
        webhook.verify(raw, signature_header, self.webhook_secret)
        payload = webhook.parse_approval(raw)
        actor = RuntimeAdapters.from_env(self.root).identity.actor(payload["actor_id"])
        return self.decide(
            payload["requirement_id"],
            actor,
            payload["outcome"],
            gate=int(payload["gate"]),
            channel="github",
        )

    def list_runs(self) -> list[dict[str, Any]]:
        return self._direct().list_runs()

    def inbox(self, actor: dict[str, Any]) -> list[dict[str, Any]]:
        return self._direct().inbox(actor)


def service_from_env(
    root: Path,
    env: Mapping[str, str] | None = None,
) -> Phase1 | TemporalPhase1:
    values = dict(os.environ if env is None else env)
    if values.get("ORCHESTRATION_MODE", "direct").lower() != "temporal":
        return Phase1(root)
    return TemporalPhase1(
        root,
        address=values.get("TEMPORAL_ADDRESS", "127.0.0.1:7233"),
        namespace=values.get("TEMPORAL_NAMESPACE", "default"),
        task_queue=values.get("TEMPORAL_TASK_QUEUE", "governed-requirements"),
    )
