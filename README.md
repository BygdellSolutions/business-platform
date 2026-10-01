# business-platform

A multi-tenant, modular business management platform. See [CLAUDE.md](CLAUDE.md) for the rules and phases, and [docs/architecture.md](docs/architecture.md) for the domain design.

## Stack

- **Frontend:** Next.js, React, TypeScript, Tailwind CSS (`frontend/`)
- **Backend:** FastAPI, SQLAlchemy, Alembic, Pydantic (`backend/`)
- **Database:** PostgreSQL (Docker Compose)

## Requirements

- Python 3.13+ and [uv](https://docs.astral.sh/uv/)
- Node.js 20+
- Docker Desktop (for PostgreSQL)

## Setup

```bash
cp .env.example .env        # then change POSTGRES_PASSWORD and DATABASE_URL to match
docker compose up -d        # start PostgreSQL
cd backend && uv sync       # install backend dependencies
uv run alembic upgrade head # create the database tables
uv run python -m app.scripts.seed_dev   # development organizations and users
cd ../frontend && npm install
```

## Development identity

There is no login yet. With `APP_ENV=development` and `AUTH_MODE=dev` (set in `.env.example`) the backend identifies the caller from the `X-Dev-User-Email` header, falling back to `DEV_USER_EMAIL`. If `AUTH_MODE=dev` is set in any other `APP_ENV`, the backend refuses to start. Without those settings there is no identity at all.

A user in several organizations picks one with `X-Organization-Id`. This only *selects* among the user's own memberships; the backend verifies the membership, and an organization the user does not belong to returns 404.

Seeded users (see `backend/app/scripts/seed_dev.py`): `fredrik@dev.test` (owner of Fredrik Horse Therapy, admin of Umeå Stable Services) and `maria@dev.test` (employee of Umeå Stable Services only). Try `GET /api/me`.

## Run

```bash
# backend — http://localhost:8000
cd backend
uv run uvicorn app.main:app --reload

# frontend — http://localhost:3000
cd frontend
npm run dev
```

The frontend calls the backend at `http://localhost:8000`. To change that, set `NEXT_PUBLIC_API_URL` in `frontend/.env.local`.

## Check that it works

- `http://localhost:8000/health` returns `{"status":"ok"}`
- `http://localhost:8000/health/db` returns `{"database":"ok"}` when PostgreSQL is reachable (503 otherwise)
- `http://localhost:3000` shows `Backend: ok`

## Tests and migrations

```bash
cd backend
uv run pytest                                  # tests (run `alembic upgrade head` first; DB tests roll back their data)
uv run alembic revision --autogenerate -m "…"  # create a migration
uv run alembic upgrade head                    # apply migrations

cd frontend
npm run lint
```
