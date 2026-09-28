# Build — skill file

Gate 5 execution. Write the product the Gate 3 UI already designed.

## Must produce

- Database schema from the BRD entities (Prisma or Alembic). SQLite file is
  the local stand-in for the locked PostgreSQL profile.
- API modules that persist those entities (GET / POST / DELETE).
- Frontend files that are the **exact approved Gate 3 JSX**, not a rewrite.
- Assembled app under `app/{requirement_id}/` that starts with `node server.js`.

## Boundary

- Do not invent files outside the path allow-list from Gate 4 (ticket branches).
- Do not merge. Senior engineer (not the author) signs Gate 5.
- Scanners are evidence, not optional colour.
- OpenHands or SWE-agent only when the ticket says so.

## Output

Runnable app + scanner notes + coverage vs tickets. No new requirements.
