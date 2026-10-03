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
cp .env.example .env        # then change POSTGRES_PASSWORD, DATABASE_URL and TEST_DATABASE_URL to match
docker compose up -d        # start PostgreSQL (development) AND postgres-test (tests, port 5433)
cd backend && uv sync       # install backend dependencies
uv run alembic upgrade head # create the development database tables
uv run python -m app.scripts.seed_dev   # development organizations and users
cd ../frontend && npm install
```

Create `frontend/.env.local` (the frontend does not read the root `.env`):

```text
BACKEND_URL=http://localhost:8000
DEV_IDENTITY=enabled        # development only: enables /dev-login
```

## Test database

Automated tests never use the development database. `docker compose up -d postgres-test` starts a **separate** PostgreSQL server (own container, port 5433, disposable tmpfs storage), configured only through `TEST_DATABASE_URL` in `.env` (the database name must end in `_test`).

- **pytest** exports `TEST_DATABASE_URL` as `DATABASE_URL` before the application is imported and rebuilds the schema from migrations at the start of every session. If `TEST_DATABASE_URL` is missing, pytest stops; it never falls back to development.
- **Playwright** runs its own backend (port 8001) connected only to the test database, and rebuilds and seeds it before every run (`python -m app.scripts.reset_test_db --seed`).
- A guard (`backend/app/scripts/reset_test_db.py`, mirrored in `frontend/e2e/env.ts`) refuses to run unless the database name ends in `_test` and it is on a different server than the development database. Tests prove this, including that data written by tests never appears in the development database.

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
- **Optimistic concurrency.** Every record carries an integer `version`, and a change must say which version it is based on, in an HTTP **`If-Match: "<version>"`** header (a header, not a body field, because `DELETE` and the lifecycle `POST`s have no body). The server compares it under the row lock it already takes; a mismatch is `409 {"code": "stale_record", "current_version": …}` and **nothing is changed**. Missing header: `428`; malformed: `400`. Which token guards what: `header_version` for `PATCH /transactions/{id}` (so a line changed elsewhere does not block a header edit); `version` for `DELETE /transactions/{id}` and `complete`/`reopen`/`cancel` (a decision about everything shown: any header, line or status change moves it); the line's own `version` for `PATCH`/`DELETE` of a line. Adding a line takes no version (it commutes with other edits) but moves the transaction's `version`. A no-op write moves nothing. Order of checks: record found in your organization (else 404, whatever the header says) → state allows the change (a completed transaction answers its own 409) → version → change. Foreign and random ids therefore never reach the version check.
- After pulling this change run `uv run alembic upgrade head` on your development database (migration `c41a7e5d9b20` adds `version`/`header_version` columns; additive, existing rows start at 1).

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

The browser never talks to FastAPI. The frontend's server (`BACKEND_URL`) does, on its behalf; see "Frontend" below.

## Check that it works

- `http://localhost:8000/health` returns `{"status":"ok"}`
- `http://localhost:8000/health/db` returns `{"database":"ok"}` when PostgreSQL is reachable (503 otherwise)
- `http://localhost:3000/dev-login` lets you sign in as a seeded user; then pick an organization

## Frontend

Next.js (App Router). See `docs/architecture.md` ("Implementation notes: frontend") for the design.

- **Organization lives in the URL:** every page is `/o/{orgId}/...`. Two tabs can work in two organizations; switching is a plain link (a full page load), so no client state or cached data can carry over.
- **BFF:** the browser calls only its own origin, `/api/o/{orgId}/...`. A route handler forwards to FastAPI and adds `X-Dev-User-Email` (httpOnly cookie) and `X-Organization-Id` (the URL) itself. Identity or organization headers sent by a client are ignored. FastAPI still verifies the user and their membership on every scoped request and answers 404 for an organization they do not belong to.
- **Development sign-in:** `/dev-login` (only when `DEV_IDENTITY=enabled`) sets an httpOnly cookie for a user the backend knows (`fredrik@dev.test`, `maria@dev.test`).
- **Decimals:** money, VAT, quantity and decimal custom fields are strings everywhere in the frontend and are never converted to JavaScript numbers (`lib/decimal.ts`; ESLint forbids number conversion in the money-handling folders). The frontend only checks that a typed value looks like a decimal; digits, range and precision are the backend's rules, and its 422 answer is shown on the field.
- **Customers** (`/o/{orgId}/customers`, `/new`, `/{id}`) and **Catalog** (`/o/{orgId}/catalog`, `/new`, `/{id}`): list with search and filters, create, edit, and deactivate/reactivate (records are deactivated, not deleted). Lists and details are read on the server; forms are client components that save through the BFF. Search, status, type and page live in the address (`?q=anna&active=inactive&type=product&page=2`), set by a plain GET form, so a list always shows what its URL says for the organization in that URL. A record id from another organization, a random id and a malformed id all show the same not-found page.

- **Horses** (`/o/{orgId}/horses`, `/new`, `/{id}`): the first relationship screens. A horse has a required **Owner** and an optional **Stable**, both customers chosen with the reusable `EntityPicker` (`components/ui/EntityPicker.tsx`); the form submits each customer's **id**, never its text. The list filters by search, status, Owner and Stable. The picker offers only active customers for a new assignment; a customer that was deactivated after being assigned is still shown ("Name (inactive)"), is not cleared, and does not block editing other fields (an unchanged owner or stable is not sent, which matches the backend). The backend decides whether a customer id may be assigned; a customer of another organization and a random id are refused with the same message on the Owner or Stable control.

- **Transactions** (`/o/{orgId}/transactions`, `/new`, `/{id}`): a list with status, customer and date filters (newest first, totals from the server), a create page (billing customer and date only; the date is prefilled with the browser's local calendar date as plain `YYYY-MM-DD`), and one page per transaction that is an editor while it is a draft and a read-only view once completed or cancelled. Lines are added either **from the catalog** (the form sends only `{item_id, quantity}`; FastAPI makes the snapshot, which then appears as stored and can be edited) or **ad-hoc** (description, unit, quantity, price, VAT as strings). An existing line always shows its own stored values, never the item's current ones. **The frontend calculates nothing**: line amounts, totals and the VAT breakdown are the server's strings. The page is a server component and the single source of truth: every successful change is followed by `router.refresh()`, and totals are marked "updating…" until the new page arrives. One change runs at a time; Complete/Reopen/Cancel wait while any editor is open; Cancel and delete ask first. Changes carry their `If-Match` version (see the Transactions API). If another tab changed the record, the open editor **keeps the user's draft**, switches Save off, and offers "Discard my edits and load the latest"; if the transaction stopped being a draft, the editor says the changes could not be saved and shows the read-only state. A tab that becomes visible again refreshes itself when no editor is open (no polling). A blocked completion shows the structured problems from FastAPI as a generic list (the custom-field editor is a later slice).

- **Custom fields in transactions:** the transaction page loads the organization's enabled field definitions for `transaction` and `transaction_line` (and the values of this transaction and its lines) on the server and shows them with the transaction and with each line. The renderer is **generic** (`components/custom-fields/`, `lib/custom-fields/`): it understands field metadata and six types (text, number, date, boolean, select, reference) and nothing about what a field is for, which a boundary test enforces (no imports of feature modules, no comparison of metadata to literals such as a field key or source, no domain words). Types are kept exactly: a number is a decimal string, a date is `YYYY-MM-DD`, a boolean has three states (not set / yes / no; `false` is a value), a select stores its option's UUID, a reference stores the record's UUID (the picker shows labels, only ids are sent). Reference choices come from the definition's generic choices endpoint (`/custom-fields/definitions/{id}/choices`), narrowed by the parent field's value when the definition says it depends on another field. Changing or clearing a field clears everything that depends on it, through any number of levels, and **the parent change and the cleared children travel in one request**. A record's fields are saved together (only the changed ones), because the backend validates a record's values as a whole (including required fields). Inactive reference targets stay displayed but are not newly assignable; a missing target is shown as "(no longer exists)". Fields are editable only while the transaction is a draft; completed and cancelled transactions show their values read-only. A blocked completion's problems appear in the banner (with links) **and at the controls they are about**. Custom-field writes carry no Sales version (they are outside the Sales concurrency contract; the backend locks the transaction, so a write and a completion cannot interleave).

## Tests and migrations

```bash
cd backend
uv run pytest                                  # uses the TEST database (docker compose up -d postgres-test)
uv run alembic revision --autogenerate -m "…"  # create a migration
uv run alembic upgrade head                    # apply migrations to the development database
uv run python -m app.scripts.reset_test_db --seed   # rebuild and seed the test database by hand

cd frontend
npm run typecheck   # next typegen + tsc
npm run lint
npm test            # Vitest: unit and component tests
npm run test:e2e    # Playwright against a real stack on the test database (needs postgres-test)
npm run check       # typecheck, lint, tests, production build
```

End-to-end tests drive the installed Microsoft Edge (`E2E_BROWSER=chrome` for Chrome), so no browser download is needed.
