# Department routing — Decomposer Agent

Tickets are assigned to organisational streams, not epics. This is the factory's
own rule set; BMAD splits by feature.

## Streams

- development — API, schema, migrations, screens
- qa — test harness work that is a ticket rather than Gate-3 test design
- devops — pipeline, IaC, secrets wiring (Sprint 0 is a prerequisite, not a stream)
- ai — models, prompts, inference, evaluation
- sales — does not receive build tickets

## Rules

- Every ticket has a path allow-list. Phase 4 builders may not write outside it.
- Every entity has exactly one owning department.
- Shared entities are spelled identically across streams (`ReturnItems`, never
  `Return_Items` in one stream and `ReturnItems` in another). Repair spelling
  deterministically; do not guess a second owner.
- Dependency order is required. A screen ticket depends on the API that feeds it.

## Output shape

Ordered tickets with id, title, department, depends_on, paths, and traceability
ids from the BRD.
