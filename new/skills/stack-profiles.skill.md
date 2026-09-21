# Stack profiles — Architect Agent

The Architect selects **one** profile and locks it for the run. Context is not
enforcement; Gate 3 records the lock. Later CI allow-list and SBOM prove it held.

## Node track

- Frontend: React (MIT)
- API: Express (MIT)
- Data access: Prisma ORM (Apache-2.0)
- Migrations: Prisma Migrate
- Tests: Vitest (MIT)
- Lint: ESLint + Prettier (MIT)
- Database: PostgreSQL

## Python track

- Frontend: React (MIT)
- API: FastAPI (MIT)
- Data access: SQLAlchemy (MIT)
- Migrations: Alembic (MIT)
- Tests: Pytest (MIT)
- Lint: Ruff (MIT)
- Database: PostgreSQL

## Rules

- Never both test runners in one project.
- Never a third runtime (JVM, .NET, Go). That fails the profile at Gate 3.
- A proprietary dependency needs a documented exception, never a default.
- Precedent retrieval is disabled. Derive this architecture from this BRD only.

## Output shape

Write `architecture.md`, ADRs, and `stack-profile.json`. Raise a clarification
question rather than inventing a page or entity the BRD does not support.
