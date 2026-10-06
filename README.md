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
AUTH_MODE=dev               # development sign-in at /dev-login (needs APP_ENV=development)
APP_ENV=development
# For the real login instead: AUTH_MODE=session and PUBLIC_ORIGIN=http://localhost:3000
```

## Test database

Automated tests never use the development database. `docker compose up -d postgres-test` starts a **separate** PostgreSQL server (own container, port 5433, disposable tmpfs storage), configured only through `TEST_DATABASE_URL` in `.env` (the database name must end in `_test`).

- **pytest** exports `TEST_DATABASE_URL` as `DATABASE_URL` before the application is imported and rebuilds the schema from migrations at the start of every session. If `TEST_DATABASE_URL` is missing, pytest stops; it never falls back to development.
- **Playwright** runs its own backend (port 8001) connected only to the test database, and rebuilds and seeds it before every run (`python -m app.scripts.reset_test_db --seed`).
- A guard (`backend/app/scripts/reset_test_db.py`, mirrored in `frontend/e2e/env.ts`) refuses to run unless the database name ends in `_test` and it is on a different server than the development database. Tests prove this, including that data written by tests never appears in the development database.

## Authentication (session mode, backend only for now)

Besides the development identity below, the backend has real authentication: `AUTH_MODE=session` (the only mode `APP_ENV=production` accepts, together with `SECURITY_KEY`; see `.env.example`). Authentication answers "who is this user?"; what the user may do in an organization is still decided only by their membership.

- **Passwords** are Argon2id (12 to 128 characters, no composition rules). **Sessions** are opaque server-side tokens (12 hours idle, 7 days absolute, at most 20 per user), kept only as a hash; the caller sends `Authorization: Bearer <token>` and, on every mutating request, `X-CSRF-Token`. Production authentication is meant to be reached through the BFF (a later step); today a client can call `POST /api/auth/login` directly in session mode.
- **Endpoints** (session mode only, 404 otherwise): `POST /api/auth/login`, `POST /api/auth/logout`, `POST /api/auth/change-password`, `POST /api/auth/setup`. Wrong credentials of every kind answer the same 401; repeated failures are throttled per source, per source and account, and across sources without ever locking an account (`docs/architecture.md`, "Login abuse protection").
- **The first user and recovery** come from the operator CLI, which never accepts a password: it prints a single-use link that lets the person choose their own.

```bash
cd backend
uv run python -m app.scripts.admin bootstrap-user --email owner@example.com --name "Ada Owner"
uv run python -m app.scripts.admin reissue-setup-link --email owner@example.com   # recovery
uv run python -m app.scripts.admin grant-org-creation --email owner@example.com    # may create organizations (revoke-org-creation takes it away)
uv run python -m app.scripts.admin disable-user --email someone@example.com       # also ends their sessions
uv run python -m app.scripts.admin enable-user  --email someone@example.com
uv run python -m app.scripts.admin purge        # expired sessions, used or expired links, old security events
```

- **Existing users** (the seeded development users included) have no credential, so they cannot log in with a password until a link is redeemed; the seed never creates one.
- After pulling this change run `uv sync` (adds `argon2-cffi`) and, on a database you want to use in session mode, `uv run alembic upgrade head` (migration `b96f2d4e8a13`; additive, existing rows are untouched).
- The browser login is described under "Frontend" below (S2). `AUTH_MODE=dev` with `/dev-login` still works for development.

## Development identity

This is the development-only identity (there is no browser login yet). With `APP_ENV=development` and `AUTH_MODE=dev` (set in `.env.example`) the backend identifies the caller from the `X-Dev-User-Email` header, falling back to `DEV_USER_EMAIL`. If `AUTH_MODE=dev` is set in any other `APP_ENV`, the backend refuses to start. Without those settings there is no identity at all.

A user in several organizations picks one with `X-Organization-Id`. This only *selects* among the user's own memberships; the backend verifies the membership, and an organization the user does not belong to returns 404.

Seeded users (see `backend/app/scripts/seed_dev.py`): `fredrik@dev.test` (owner of Fredrik Horse Therapy, admin of Umeå Stable Services) and `maria@dev.test` (employee of Umeå Stable Services only). Try `GET /api/me`.

## Customers API

`/api/customers` (`POST`, `GET ?q=&limit=&offset=`, `GET|PATCH|DELETE /{id}`) is the first tenant-owned resource. Every customer belongs to exactly one organization, taken from the validated tenant context — never from the request. A body containing `organization_id` is rejected with 422, and a customer UUID from another organization returns 404, exactly like a nonexistent one. Example, as the shared dev user in the second organization:

```bash
curl http://localhost:8000/api/customers \
  -H "X-Dev-User-Email: fredrik@dev.test" \
  -H "X-Organization-Id: 00000000-0000-4000-8000-0000000000b2"
```

**Billing profile.** A customer also has seven optional, free-text fields: `address_line1`, `address_line2`, `postal_code`, `city`, `country_code`, `registration_number`, `vat_number`. Blank means "not set" (stored as NULL; send `null` or blank text to clear one). Only the shape of `country_code` is checked (two capital letters; lower case is upper-cased); there is no jurisdiction-specific validation.

## Organization settings API

`GET /api/organization` (any member) and `PATCH /api/organization` (**owner and admin only**) read and change the settings of the *active* organization (there is no id in the path or body). Fields: `name`, `legal_name`, the same seven profile fields as customers, and `default_currency`; the response adds `default_currency_locked` and `default_currency_lock_reason`.

- **Currency is never assumed.** `default_currency` is empty until an owner or admin sets it (three capital letters; only the shape is checked). A new transaction **copies** the organization's currency when it is created and keeps it forever (a database trigger refuses any change); an organization with no currency cannot create transactions (`409 currency_not_configured`).
- **Changing the currency is refused once it would reinterpret stored prices** (`409 currency_locked`): when the current default is set, a different value is refused if the organization has any item or transaction. The first setting is always allowed. This is a **temporary rule** until a proper currency/repricing model exists.
- **Transactions that predate currencies** keep no currency (shown as "No currency recorded"). `GET /api/transactions/currency-status` counts them; `POST /api/transactions/assign-currency` with `{"currency": "SEK"}` (owner/admin) is the one explicit way to give them the organization's currency (the value must equal the default; only transactions without a currency are touched).
- **Development databases:** after pulling this change run `uv run alembic upgrade head` (migrations `d52b8f1a7c34`, `e63c9a2b8d45`; additive, no data is relabelled) and re-run `uv run python -m app.scripts.seed_dev`, which gives the **seeded** organizations and their seeded transactions SEK and a small profile (it fills only empty values, never overwriting what you edited).

```bash
curl -X PATCH http://localhost:8000/api/organization \
  -H "X-Dev-User-Email: fredrik@dev.test" -H "X-Organization-Id: 00000000-0000-4000-8000-0000000000a1" \
  -H "Content-Type: application/json" -d '{"legal_name":"Fredrik Horse Therapy AB","city":"Umeå","country_code":"SE"}'
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
- Each transaction has a `currency`, copied from the organization at creation (see the Organization settings API). `complete`, `reopen` and `cancel` ask the core lifecycle seam first, so other modules can veto them (Invoicing vetoes reopen and cancel of a transaction that is on a draft or issued invoice).
- Lines: `POST /api/transactions/{id}/lines`, `PATCH|DELETE /api/transactions/{id}/lines/{line_id}`. List filters: `?status=`, `?billing_customer_id=`, `?date_from=`, `?date_to=`.
- **Optimistic concurrency.** Every record carries an integer `version`, and a change must say which version it is based on, in an HTTP **`If-Match: "<version>"`** header (a header, not a body field, because `DELETE` and the lifecycle `POST`s have no body). The server compares it under the row lock it already takes; a mismatch is `409 {"code": "stale_record", "current_version": …}` and **nothing is changed**. Missing header: `428`; malformed: `400`. Which token guards what: `header_version` for `PATCH /transactions/{id}` (so a line changed elsewhere does not block a header edit); `version` for `DELETE /transactions/{id}` and `complete`/`reopen`/`cancel` (a decision about everything shown: any header, line or status change moves it); the line's own `version` for `PATCH`/`DELETE` of a line. Adding a line takes no version (it commutes with other edits) but moves the transaction's `version`. A no-op write moves nothing. Order of checks: record found in your organization (else 404, whatever the header says) → state allows the change (a completed transaction answers its own 409) → version → change. Foreign and random ids therefore never reach the version check.
- After pulling this change run `uv run alembic upgrade head` on your development database (migration `c41a7e5d9b20` adds `version`/`header_version` columns; additive, existing rows start at 1).

```bash
curl -X POST http://localhost:8000/api/transactions \
  -H "X-Dev-User-Email: maria@dev.test" -H "Content-Type: application/json" \
  -d '{"billing_customer_id":"<customer id>","lines":[{"item_id":"<item id>","quantity":"1"}]}'
```

## Invoices API (Invoicing module, backend only)

`/api/invoices` and `/api/invoiceable-transactions` (`backend/app/modules/invoicing/`) turn **whole completed transactions** into invoices. The invoicing screens are described under the frontend; the PDF of an issued invoice is described below.

- **What can share an invoice:** completed transactions of the **same billing customer** and the **same, non-empty currency**, none of which is already on a draft or issued invoice. No transaction or line is ever split. A transaction without a currency (one that predates currencies and was not assigned one) is not invoiceable. Invoicing state is derived from invoicing records only; Sales stores nothing about it.
- **Draft, then issue.** `POST /api/invoices` with `{"transaction_ids": [...], "invoice_date"?, "due_date"?, "description"?}` creates a **draft** that reserves its transactions and has **no number**. The customer, currency and every amount come from the locked source transactions (they are never accepted from the caller); line amounts are copied verbatim from the stored Sales lines, and the totals and VAT breakdown are stored sums of them. `PATCH /api/invoices/{id}` edits only the draft's dates and description. `POST /api/invoices/{id}/issue` re-verifies that the sources still match what the draft reserved (otherwise `409 source_changed`, nothing is issued), re-takes the customer, organization and custom-field content as of issuance, allocates the number and freezes the invoice. `DELETE /api/invoices/{id}` deletes a **draft** and releases its transactions. An issued invoice can never be changed or deleted (the API, and invoice-local database triggers, refuse); there is no void, no credit note and no payment yet.
- **Concurrency:** `PATCH`, `issue` and `DELETE` require `If-Match: "<version>"` like Sales (428 missing, 400 malformed, `409 stale_record` when stale; foreign and random ids are the one 404).
- **Reservation:** a transaction on a draft **or** issued invoice cannot be reopened or cancelled (`409` with an `invoice.reserved` problem). Delete the draft to release it. Sales does not import Invoicing; the rule is a validator on the core lifecycle seam.
- **Numbers** come from a counter per organization and series, allocated only at issuance in the same database transaction (a failed issuance gives its number back; an issued number is never reused; a draft has none). They are plain integers (`number_text` stores the label). This is not a legal "gapless numbering" guarantee: whether a jurisdiction requires that is policy that can be layered on later.
- **An issued invoice is a self-contained document.** `GET /api/invoices/{id}` returns the stored customer and issuer snapshots, lines with their custom-field copies, and the VAT breakdown, reading **only** the invoicing tables. Renaming a customer, changing an item or a field, or deleting source records later does not change it (tests prove this, including by recording the SQL).
- **Who:** every member may read; owner, admin and accountant may create, edit, issue and delete drafts.
- **Invoice PDF:** `GET /api/invoices/{id}/pdf` returns the PDF of an **issued** invoice (any member who can read it; a draft is `409 invoice_not_issued`, a foreign or unknown id the usual 404). The first request renders it from the stored invoice and stores it in `invoice_pdfs`; every later request returns exactly the stored bytes, so a later change of the template, fonts or library never alters an old invoice. Text the renderer cannot draw correctly (scripts that need shaping or right-to-left layout, emoji) is refused with `422 unsupported_characters` naming the characters, and nothing is stored. English labels, locale-neutral numbers, no payment details. See `docs/architecture.md`, "Invoice PDF (as built)".
- `GET /api/invoiceable-transactions` lists what can be invoiced (`?customer_id=&date_from=&date_to=`); `GET /api/invoices/by-transaction?ids=a,b` tells for each transaction `none`, `draft` or `invoiced`.
- After pulling this change run `uv run alembic upgrade head` on your development database (migration `f74d0b3c9e56`; additive, existing rows are untouched).
- After pulling the PDF change run `uv run alembic upgrade head` again (migration `a85e1c4d7f90`: one new table, `invoice_pdfs`, and its protection trigger; additive). The renderer needs its bundled fonts, which are part of the repository (`backend/app/modules/invoicing/pdf/fonts/`, kept binary by `.gitattributes`); run `uv sync` for ReportLab.

```bash
curl -X POST http://localhost:8000/api/invoices \
  -H "X-Dev-User-Email: fredrik@dev.test" -H "X-Organization-Id: 00000000-0000-4000-8000-0000000000a1" \
  -H "Content-Type: application/json" -d '{"transaction_ids":["<id of a completed transaction>"]}'
```

## Custom fields API

Organizations configure extra fields on supported records without schema changes (`/api/custom-fields`, `backend/app/modules/custom_fields/`). Modules register what they expose on a core registry: today transactions and transaction lines accept custom fields, and customers and horses can be referenced.

- **Definitions** (`GET|POST /definitions`, `GET|PATCH /definitions/{id}`; owner/admin to write): types `text`, `number`, `date`, `boolean`, `select` (with stable option ids) and `reference`. A reference field can depend on another field, for example *Owner* (a customer) then *Horse* (a horse whose owner is the selected customer). Definitions are disabled, never deleted, and structural properties cannot change.
- **Choices** (`GET /definitions/{id}/choices?depends_on_value=&q=`): what a form offers for selects and references.
- **Values** (`GET|PATCH /entities/{entity_type}/{entity_id}/values`, and `GET /values?entity_type=&entity_ids=` for tables). Numbers are decimal strings; references store the record id and display live data.
- **Flag filter:** `?flag=required|show_in_form|show_in_table|show_on_invoice` on `GET /definitions`, `GET /entities/{type}/{id}/values` and `GET /values` returns only the definitions (and values) with that property, for example the fields eligible for a future invoice.
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
- `http://localhost:8000/health/ready` returns `{"status":"ready"}` when PostgreSQL is reachable AND its Alembic revision is exactly this code's head (`503 {"status":"unready"}` otherwise, e.g. before `alembic upgrade head`)
- `http://localhost:3000/api/health` (frontend liveness) and `http://localhost:3000/api/ready` (the whole chain)
- `http://localhost:3000/dev-login` lets you sign in as a seeded user; then pick an organization

## Frontend

Next.js (App Router). See `docs/architecture.md` ("Implementation notes: frontend") for the design.

- **Organization lives in the URL:** every page is `/o/{orgId}/...`. Two tabs can work in two organizations; switching is a plain link (a full page load), so no client state or cached data can carry over.
- **BFF:** the browser calls only its own origin, `/api/o/{orgId}/...`. A route handler forwards to FastAPI and adds `X-Dev-User-Email` (httpOnly cookie) and `X-Organization-Id` (the URL) itself. Identity or organization headers sent by a client are ignored. FastAPI still verifies the user and their membership on every scoped request and answers 404 for an organization they do not belong to.
- **Authentication modes** (frontend `.env.local`, read only by `lib/auth/config.ts`): `AUTH_MODE=dev` with `APP_ENV=development` for the development sign-in, or `AUTH_MODE=session` with `PUBLIC_ORIGIN` (the canonical browser origin; https outside development) for the real login; anything else means no identity at all. `APP_ENV` defaults to production. The old `DEV_IDENTITY` switch no longer does anything. Optional: `TRUSTED_PROXY_HOPS` (default 0).
- **Creating an organization (S3):** a signed-in user whose account has `can_create_organizations` (set by the operator, never by a role) creates one at `/organizations/new` (reached from the home page and the organization switcher) and becomes its owner in the same database transaction (`POST /api/organizations`, not tenant-scoped; the owner is always the authenticated user). The form asks for a name and an explicitly chosen currency (nothing is assumed); the rest of the profile is completed in Settings. The browser then navigates to `/o/{id}`: the URL stays the only tenant selector. A retry key makes a lost response safe to retry. Grant or revoke the right with `python -m app.scripts.admin grant-org-creation|revoke-org-creation --email ...` (there is no application UI for it). Details: `docs/architecture.md`, "Organization onboarding (S3 as built)".
- **Membership administration (S4):** owners and admins manage members at `/o/{orgId}/members` (list, change role, remove); every member can leave from the shell header. Owner: any role for others, removal of others, may step down or leave while another owner remains. Admin: only accountants, employees and viewers, to those roles only. Everyone else: nothing but leaving. Each change is decided by the backend inside a transaction that first locks the organization's membership rows (ascending id) and re-reads them; the last owner can never be demoted, removed or leave (`409 last_owner`), with a deferred database trigger as a backstop. Removing a member never deletes the user or ends their sessions. An organization created before every organization had an owner can be repaired by the operator: `python -m app.scripts.repair owner --organization-id <uuid> --email <existing member>`. Details: `docs/architecture.md`, "Membership administration (S4 as built)".
- **Invitations (S5):** owners and admins invite people at `/o/{orgId}/members` (an owner may invite any role, an admin only accountant/employee/viewer). There is no email delivery: the administrator copies a link (`/invite#<token>`) that is shown **once** and cannot be retrieved again (regenerate to get a new one). The secret is in the URL fragment (never sent to a server), the invite page removes it from the address at once and sends it only in POST bodies. An existing account signs in on the invite page and joins (it must be the invited email; the wrong account is refused without consuming the invitation); a new person creates an account (name and password; user, credential, membership and session are created in one transaction). An invitation never changes an existing membership. Details: `docs/architecture.md`, "Invitations (S5 as built)". Setting: `INVITATION_TTL_DAYS` (default 7).
- **Real login (session mode):** `/login` (email and password; one generic failure message), `/setup#<token>` (set a password with the operator's single-use link, the secret read from the URL fragment and removed at once), sign out in the shell. The browser holds only protected cookies (`bp_session` HttpOnly; `bp_csrf` readable for the CSRF header; `__Host-` prefixed and Secure over https); the BFF turns the session cookie into `Authorization: Bearer` for FastAPI and checks Origin (against `PUBLIC_ORIGIN`) and the CSRF pair on every change. Nothing the browser sends can name a user, an organization authority or a role. Details: `docs/architecture.md`, "Browser authentication (S2 as built)".
- **Development sign-in:** `/dev-login` (only with `AUTH_MODE=dev` and `APP_ENV=development`) sets an httpOnly cookie for a user the backend knows (`fredrik@dev.test`, `maria@dev.test`).
- **Decimals:** money, VAT, quantity and decimal custom fields are strings everywhere in the frontend and are never converted to JavaScript numbers (`lib/decimal.ts`; ESLint forbids number conversion in the money-handling folders). The frontend only checks that a typed value looks like a decimal; digits, range and precision are the backend's rules, and its 422 answer is shown on the field.
- **Customers** (`/o/{orgId}/customers`, `/new`, `/{id}`) and **Catalog** (`/o/{orgId}/catalog`, `/new`, `/{id}`): list with search and filters, create, edit, and deactivate/reactivate (records are deactivated, not deleted). Lists and details are read on the server; forms are client components that save through the BFF. Search, status, type and page live in the address (`?q=anna&active=inactive&type=product&page=2`), set by a plain GET form, so a list always shows what its URL says for the organization in that URL. A record id from another organization, a random id and a malformed id all show the same not-found page.

- **Horses** (`/o/{orgId}/horses`, `/new`, `/{id}`): the first relationship screens. A horse has a required **Owner** and an optional **Stable**, both customers chosen with the reusable `EntityPicker` (`components/ui/EntityPicker.tsx`); the form submits each customer's **id**, never its text. The list filters by search, status, Owner and Stable. The picker offers only active customers for a new assignment; a customer that was deactivated after being assigned is still shown ("Name (inactive)"), is not cleared, and does not block editing other fields (an unchanged owner or stable is not sent, which matches the backend). The backend decides whether a customer id may be assigned; a customer of another organization and a random id are refused with the same message on the Owner or Stable control.

- **Transactions** (`/o/{orgId}/transactions`, `/new`, `/{id}`): a list with status, customer and date filters (newest first, totals from the server), a create page (billing customer and date only; the date is prefilled with the browser's local calendar date as plain `YYYY-MM-DD`), and one page per transaction that is an editor while it is a draft and a read-only view once completed or cancelled. Lines are added either **from the catalog** (the form sends only `{item_id, quantity}`; FastAPI makes the snapshot, which then appears as stored and can be edited) or **ad-hoc** (description, unit, quantity, price, VAT as strings). An existing line always shows its own stored values, never the item's current ones. **The frontend calculates nothing**: line amounts, totals and the VAT breakdown are the server's strings. The page is a server component and the single source of truth: every successful change is followed by `router.refresh()`, and totals are marked "updating…" until the new page arrives. One change runs at a time; Complete/Reopen/Cancel wait while any editor is open; Cancel and delete ask first. Changes carry their `If-Match` version (see the Transactions API). If another tab changed the record, the open editor **keeps the user's draft**, switches Save off, and offers "Discard my edits and load the latest"; if the transaction stopped being a draft, the editor says the changes could not be saved and shows the read-only state. A tab that becomes visible again refreshes itself when no editor is open (no polling). A blocked completion shows the structured problems from FastAPI as a generic list (the custom-field editor is a later slice).

- **Settings** (`/o/{orgId}/settings`): the organization's name, legal name, default currency and business profile. Owners and admins get the form; every other role sees the same values read-only (the server refuses their changes anyway). The currency box is switched off, with the reason, once items or transactions exist. If transactions without a currency exist, an "Earlier transactions" panel explains it and, for owners/admins, offers a confirmed "Assign SEK to these transactions" (never done automatically). Customer forms have a "Billing details" group with the seven profile fields; transactions show their currency (or "No currency recorded").

- **Invoices** (`/o/{orgId}/invoices`, `/new`, `/{invoiceId}`): the minimal Invoicing screens, all through the same tenant-safe BFF (`/api/o/{orgId}/invoices`, `.../invoiceable-transactions`). No email, payments, credit notes, export, void/cancel or visual polish.
  - **List:** invoice number (the stored text) or `Draft`, the invoice's own customer name, invoice and due date, status, currency and the server's net/VAT/gross strings. Only the filters the backend supports (status, customer, invoice-date range, search over customer name and number) and paging; nothing is filtered or sorted in the browser.
  - **Create:** the list comes from `GET /api/invoiceable-transactions` (completed, with a currency, on no invoice). Once one transaction is selected only the same billing customer **and** currency can be selected (the others are disabled and say why); the customer and currency are shown. The request carries the chosen ids and the approved header fields (dates, description), **never** a customer, currency, organization or amount. There is no combined total: the backend offers no authoritative aggregate for a selection and the frontend adds no money. If a transaction stopped being invoiceable meanwhile, FastAPI's structured conflict is shown, the transactions it names are dropped from the selection and eligibility is re-read.
  - **Invoice page:** renders the stored document only: both party snapshots, lines with their custom-field snapshots, the stored VAT breakdown and totals, currency, dates, description, status and version. Source transactions appear as **navigation/audit links** in a visibly secondary section, labelled with the date the invoice recorded; the page never reads a customer, item, horse or custom-field record to fill the document in (a boundary test enforces it, and component tests make the live records deliberately disagree). Custom-field snapshots use a small generic read-only renderer (`components/snapshots/`) whose only input is the stored array.
  - **Draft:** edit details (invoice date, due date, description) with `If-Match` = the version the editor was opened on; changed fields only, no request for a no-op; a stale refusal keeps the user's draft, shows the server's state and offers "Discard my edits and load the latest". **Issue** and **Delete draft** each ask first (issuing explains that the invoice then cannot be edited or deleted; deleting says the transactions become invoiceable again) and carry the current version. The number shown after issuing is the one the server returned.
  - **Unknown outcomes:** a network failure or server error while issuing or deleting is never taken as "it failed": the invoice is re-read, and until that check has answered no new attempt is offered. A stale or "already issued" answer replaces the draft screen with the authoritative state. A tab that becomes visible refreshes itself only when no editor is open and no change is running.
  - **Issued:** completely read-only (no edit, delete or issue control, no void/cancel). The one control is **Download PDF**, for every role: the browser asks the BFF for `/invoices/{id}/pdf`, which passes the one binary resource through only after validating it (exact content type, `%PDF-`, length, ETag equal to the SHA-256 of the body) and rebuilds its headers; the button shows "Preparing PDF…" while the first request makes the file and explains failures (not found, not issued, characters the renderer cannot draw, server error). A draft has no PDF control.
  - **Roles:** every member can read; only owner, admin and accountant are offered controls (presentation only: FastAPI authorizes every request, and a test forges BFF requests as an employee).

- **Custom fields in transactions:** the transaction page loads the organization's enabled field definitions for `transaction` and `transaction_line` (and the values of this transaction and its lines) on the server and shows them with the transaction and with each line. The renderer is **generic** (`components/custom-fields/`, `lib/custom-fields/`): it understands field metadata and six types (text, number, date, boolean, select, reference) and nothing about what a field is for, which a boundary test enforces (no imports of feature modules, no comparison of metadata to literals such as a field key or source, no domain words). Types are kept exactly: a number is a decimal string, a date is `YYYY-MM-DD`, a boolean has three states (not set / yes / no; `false` is a value), a select stores its option's UUID, a reference stores the record's UUID (the picker shows labels, only ids are sent). Reference choices come from the definition's generic choices endpoint (`/custom-fields/definitions/{id}/choices`), narrowed by the parent field's value when the definition says it depends on another field. Changing or clearing a field clears everything that depends on it, through any number of levels, and **the parent change and the cleared children travel in one request**. A record's fields are saved together (only the changed ones), because the backend validates a record's values as a whole (including required fields). Inactive reference targets stay displayed but are not newly assignable; a missing target is shown as "(no longer exists)". Fields are editable only while the transaction is a draft; completed and cancelled transactions show their values read-only. A blocked completion's problems appear in the banner (with links) **and at the controls they are about**. Custom-field writes carry no Sales version (they are outside the Sales concurrency contract; the backend locks the transaction, so a write and a completion cannot interleave).

## Containers (production images, slices D1 and D2)

Two production images exist (nothing is deployed yet): `backend/Dockerfile` (FastAPI, uid 10001, **one Uvicorn worker**,
port 8000; it never runs migrations; health check = readiness) and `frontend/Dockerfile` (Next.js standalone, non-root,
port 3000; ONE image for every environment: all configuration is read at run time; health check = liveness). Build and run:

```bash
docker build -t business-platform-backend backend
docker build -t business-platform-frontend frontend
```

`deploy/compose.rehearsal.yml` is a local, production-LIKE stack for proving the images (frontend published on loopback
only; backend and a disposable PostgreSQL private); it is not the production topology (Coolify proxy, private backend,
Coolify-managed PostgreSQL 17). Its startup order is `postgres -> db-bootstrap -> migrate -> backend (ready) -> frontend`;
migrations run ONLY in the one-shot job `python -m app.scripts.migrate` (the owner role, under an advisory lock; exit
non-zero on any failure), never in the web process, and the backend runs as a restricted role that cannot change the schema.
The image, topology and orchestration tests (need Docker): `cd backend && uv run pytest ../deploy/tests -q`. Details:
`docs/architecture.md`, "Container foundation (D1 as built)" and "Deployment configuration, health and trust (D2 as built)".

**Production configuration (fail closed; nothing is guessed or defaulted):**

| backend (`APP_ENV=production`) | frontend (`APP_ENV=production`) |
|---|---|
| `AUTH_MODE=session`, `DATABASE_URL` (the restricted app role), `SECURITY_KEY`, `PUBLIC_ORIGIN` (https, not localhost), `BFF_INTERNAL_SECRET`, `CORS_ORIGINS=[]` | `AUTH_MODE=session`, `PUBLIC_ORIGIN` (https), `BACKEND_URL` (a private/internal address), `BFF_INTERNAL_SECRET` (the same value) |
| refused: `AUTH_MODE=dev/disabled`, `DEV_USER_EMAIL`, `TEST_DATABASE_URL`, `MIGRATION_DATABASE_URL`, wildcard/localhost CORS | optional `TRUSTED_PROXY_HOPS` (stays 0 until the real proxy chain is verified) |

The migration job takes `MIGRATION_DATABASE_URL` (the schema owner) and `RUNTIME_DB_ROLE`; the one-time role bootstrap
(`python -m app.scripts.bootstrap_roles`) is described in the architecture notes.

## Continuous integration (slice D3)

`.github/workflows/ci.yml` runs on every pull request and push to `main`: `workflows` (actionlint), `backend`, `frontend`,
`containers`, `e2e-dev` and `e2e-session` in parallel, plus one aggregate check, **`ci-gate`**, which is the one to require in
branch protection. Everything runs on disposable infrastructure generated by the job itself (a tmpfs PostgreSQL 17, masked random
passwords, Chromium); no repository secret, real database or environment is touched. `.github/workflows/audit.yml` runs
dependency and image audits weekly and is informational. Reproduce a job locally:

```bash
# backend            (docker compose up -d postgres-test first)
cd backend && uv sync --frozen
uv run pytest -q --ignore=tests/test_migrate_runner.py --ignore=tests/test_db_roles.py
uv run pytest -q tests/test_migrate_runner.py && uv run pytest -q tests/test_db_roles.py
# frontend
cd frontend && npm ci && npm run typecheck && npm run lint && npm run test && npm run build
# containers         (needs Docker)
cd backend && uv run pytest ../deploy/tests -q
# e2e                (npx playwright install --with-deps chromium once)
cd frontend && E2E_BROWSER=chromium npm run test:e2e
cd frontend && E2E_BROWSER=chromium E2E_BFF_SECRET=<any> E2E_SECURITY_KEY=<32+ chars> E2E_PASSWORD=<12+ chars> npm run test:e2e:session
# workflows
docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:1.7.7
```

Details (job graph, the disposable-database guard, generated credentials, caching, artifacts, the required-check recommendation, the
audit baseline and policy): `docs/architecture.md`, "Continuous integration (D3 as built)".

## Backup, restore and runbooks (slice D4)

Nightly logical backups of the whole database (`pg_dump`, custom format, with a manifest), a restore into a **separate, scratch**
database, and a read-only verification of the result. A backup is not verified until it has been restored into an isolated database and
checked. Recovery point objective: up to 24 hours. All commands run from the backend image (`python -m app.scripts.<tool>`), each taking
its database from one explicit variable (never `DATABASE_URL`):

```bash
BACKUP_DATABASE_URL=...  python -m app.scripts.backup  --output /backups/bp-<label>-<UTC>.dump --label <label>
RESTORE_DATABASE_URL=... RUNTIME_DB_ROLE=... python -m app.scripts.restore --dump /backups/bp-....dump
VERIFY_DATABASE_URL=... [VERIFY_APP_DATABASE_URL=...] python -m app.scripts.verify_restore --manifest /backups/bp-....dump.manifest.json --alembic-check
STATS_DATABASE_URL=...   python -m app.scripts.db_stats
# the full rehearsal, on disposable containers only (Docker needed; also a manual/weekly GitHub workflow, never a merge gate):
cd backend && uv run pytest ../deploy/drill -q -s
```

Runbooks: `docs/backup-restore.md` (commands, guards, roles, manifest, off-host and retention contract, disaster recovery, drill log) and
`docs/deployment.md` (topology, production configuration, migration gating and the pre-migration backup contract, first operator,
rollback). Nothing is deployed and no storage is configured yet (D5).

## Tests and migrations

End-to-end tests have two runs, never at the same time and never against the development database: `npm run test:e2e` (the dev identity, ports 8001/3100) and `npm run test:e2e:session` (real authentication, ports 8002/3101; users get their passwords through the operator CLI's setup link in the disposable test database). Both need `docker compose up -d postgres-test`.

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
