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

## Customers API

`/api/customers` (`POST`, `GET ?q=&limit=&offset=`, `GET|PATCH|DELETE /{id}`) is the first tenant-owned resource. Every customer belongs to exactly one organization, taken from the validated tenant context — never from the request. A body containing `organization_id` is rejected with 422, and a customer UUID from another organization returns 404, exactly like a nonexistent one. Example, as the shared dev user in the second organization:

```bash
curl http://localhost:8000/api/customers \
  -H "X-Dev-User-Email: fredrik@dev.test" \
  -H "X-Organization-Id: 00000000-0000-4000-8000-0000000000b2"
```

## Items API

`/api/items` (same shape as Customers, plus `?type=service|product&active=true|false` filters) is the catalog of services and products. Items reuse the same tenant-scoped infrastructure and the same isolation tests (`backend/tests/test_tenant_isolation_contract.py` runs every tenant-owned resource through one contract).

Money is exact: `price_ex_vat` (excluding VAT, `NUMERIC(12,2)`) and `vat_rate` (percent, `NUMERIC(5,2)`) are sent as decimal **strings**, and returned as strings with two decimals. JSON numbers with decimals are rejected.

```bash
curl -X POST http://localhost:8000/api/items \
  -H "X-Dev-User-Email: maria@dev.test" -H "Content-Type: application/json" \
  -d '{"type":"service","name":"Saddle fitting","unit":"hour","price_ex_vat":"850.00","vat_rate":"25"}'
```

## Horses API (Equine module)

`/api/horses` is the first optional domain module (`backend/app/modules/equine/`). A horse has a required **owner** and an optional **stable**, both references to Customers of the same organization (`owner_customer_id`, `stable_customer_id`), plus `name`, optional `birth_year`, `sex` (`mare`, `stallion`, `gelding`) and free-text `breed`. Owner, stable and the future billing customer are separate concepts; a horse has no billing field.

- References are checked in the active organization (a foreign or random customer id gives the same 422) and enforced again by composite foreign keys in PostgreSQL.
- A deactivated customer cannot be newly assigned, but existing horses keep working. A customer that a horse references cannot be deleted (`409`); deactivate it instead.
- Filters: `?q=`, `?owner_customer_id=`, `?stable_customer_id=`, `?active=`, `limit`, `offset`.

## Transactions API (Sales)

`/api/transactions` is the generic, industry-neutral sales record (`backend/app/modules/sales/`). A **transaction** has one billing customer, a date, a lifecycle status and any number of **lines**. Sales knows nothing about horses or any other industry; that context will be attached later through custom fields.

- **Lines** copy the Item's description, unit, price and VAT when added (a snapshot: editing the Item later changes nothing on existing lines). Any of those four can be overridden per line, and a line without an `item_id` is an ad-hoc line that must supply all four. The quantity (`NUMERIC(12,3)`, greater than 0), price and VAT are sent as decimal **strings**; JSON numbers with decimals are rejected.
- **Amounts** are calculated per line (`net = round_half_up(quantity × price, 2)`, `vat = round_half_up(net × rate / 100, 2)`, `gross = net + vat`), stored on the line, and kept consistent by database CHECK constraints. Transaction totals and the VAT breakdown are sums of the stored line amounts. Clients never send amounts.
- **Lifecycle:** `draft` → `completed` (finalized, ready for future invoicing) → back to `draft` via `reopen`, or `cancelled` (final). Use `POST /api/transactions/{id}/complete|reopen|cancel`. Only drafts can be edited or deleted (otherwise `409`).
- Customers and Items that a transaction references cannot be deleted (`409`); deactivate them instead.
- Lines: `POST /api/transactions/{id}/lines`, `PATCH|DELETE /api/transactions/{id}/lines/{line_id}`. List filters: `?status=`, `?billing_customer_id=`, `?date_from=`, `?date_to=`.

```bash
curl -X POST http://localhost:8000/api/transactions \
  -H "X-Dev-User-Email: maria@dev.test" -H "Content-Type: application/json" \
  -d '{"billing_customer_id":"<customer id>","lines":[{"item_id":"<item id>","quantity":"1"}]}'
```

## Custom fields API

Organizations configure extra fields on supported records without schema changes (`/api/custom-fields`, `backend/app/modules/custom_fields/`). Modules register what they expose on a core registry: today transactions and transaction lines accept custom fields, and customers and horses can be referenced.

- **Definitions** (`GET|POST /definitions`, `GET|PATCH /definitions/{id}`; owner/admin to write): types `text`, `number`, `date`, `boolean`, `select` (with stable option ids) and `reference`. A reference field can depend on another field, for example *Owner* (a customer) then *Horse* (a horse whose owner is the selected customer). Definitions are disabled, never deleted, and structural properties cannot change.
- **Choices** (`GET /definitions/{id}/choices?depends_on_value=&q=`): what a form offers for selects and references.
- **Values** (`GET|PATCH /entities/{entity_type}/{entity_id}/values`, and `GET /values?entity_type=&entity_ids=` for tables). Numbers are decimal strings; references store the record id and display live data.
- **Required fields** must be filled before a transaction can be completed. The completion answers `409` with `problems` naming the record, field and label to fix, and completed transactions lock their custom values.
- Referenced customers and horses cannot be deleted (`409`).

The dev seed configures Owner and Horse on transaction lines in both organizations.

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
