"""Real Temporal worker entry point."""

from __future__ import annotations

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from temporalio.client import Client
from temporalio.worker import Worker

from phase1.temporal_activities import RequirementActivities
from phase1.temporal_workflow import RequirementWorkflow


async def run_worker() -> None:
    address = os.getenv("TEMPORAL_ADDRESS", "127.0.0.1:7233")
    namespace = os.getenv("TEMPORAL_NAMESPACE", "default")
    task_queue = os.getenv("TEMPORAL_TASK_QUEUE", "governed-requirements")
    root = Path(os.getenv("RUNTIME_ROOT", "/data"))
    client = await Client.connect(address, namespace=namespace)
    activities = RequirementActivities(root)
    with ThreadPoolExecutor(
        max_workers=int(os.getenv("ACTIVITY_WORKERS", "8"))
    ) as executor:
        worker = Worker(
            client,
            task_queue=task_queue,
            workflows=[RequirementWorkflow],
            activities=[
                activities.submit,
                activities.turn,
                activities.decide,
                activities.edit_brd,
                activities.escalate,
            ],
            activity_executor=executor,
        )
        ready_file = os.getenv("WORKER_READY_FILE")
        if ready_file:
            Path(ready_file).touch()
        await worker.run()


def main() -> None:
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
