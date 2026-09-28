# QA — test design before code

Design tests from the approved BRD and screens. No application source yet.
Framework comes from the locked stack profile (Vitest / Pytest / e2e runner).

## Rules

- Every test traces to a BRD requirement id (`### Rxx` / capability id).
- At least one test is marked critical (concurrency, auth, or money/booking conflict).
- Cover happy path, one clear failure, and one edge the BRD names.
- Name tests as behaviour, not files (`book a room for a time slot`, not `test_booking`).
- Prefer product language from the BRD over generic "user can submit form".

## Output shape

JSON list of tests: id, name, criterion (trace id), critical boolean.
Do not invent requirements that are not in the BRD.
