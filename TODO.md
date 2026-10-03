# TODO — business-platform

## Now
- [x] Install Docker Desktop and run `docker compose up -d`
- [x] Verify `/health/db` returns `{"database":"ok"}` (backend can connect to PostgreSQL)
- [x] Set Git `user.name` / `user.email` and make the first commit
- [x] Verify frontend runs (`npm run dev`) and reaches the backend
- [x] Create Organization model and migration (start of Phase 1)
- [x] Users, organization memberships and development identity (`/api/me`, tenant context)
- [x] Tenant-scoped query helpers, Customer model/migration, minimal tenant-safe Customer API, cross-tenant tests
- [x] Catalog: Item model/migration, tenant-safe Items API, exact money/VAT handling, shared tenant-isolation contract
- [x] Equine module: Horse with Customer owner/stable references, composite tenant-safe FKs, module boundary test
- [x] Sales: Transactions (header + lines), item snapshots, stored CHECK-enforced line amounts, lifecycle actions, row locking, tenant contract
- [x] Custom fields (UDF) engine: core entity registry, lifecycle validation seam, `require_role`, typed value storage, dependent references, delete guard, required fields at completion

## Next
- [x] Frontend slice 1 (foundations): BFF, dev identity, `/o/{orgId}` shell, organization switcher, dashboard, test infrastructure and tenant-isolation e2e tests
- [x] Frontend slice 2: Customers and Catalog (list, search, filters, create, edit, deactivate/reactivate) with the shared UI primitives and tenant-isolation e2e tests
- [x] Frontend slice 3: Horses with Owner and Stable pickers (first reusable EntityPicker)
- [x] Sales optimistic concurrency (`If-Match` versions on transactions, headers and lines) with committed-data concurrency tests
- [x] Frontend slice 4: Transactions (list, create-then-edit, line editor, backend-owned totals, lifecycle, stale-tab handling)
- [ ] Run `uv run alembic upgrade head` on the development database (migration `c41a7e5d9b20`); the dev backend needs it before it can serve transactions
- [x] Frontend slice 5: generic custom-field renderer, dependent references, completion problems mapped to lines and controls, read-only completed transactions
- [x] Development database migrated to `c41a7e5d9b20` (done before slice 5)
- [x] Invoicing design approved (recorded in `docs/architecture.md`, "Invoicing design (approved, NOT implemented)")
- [x] Invoicing prerequisites: organization and customer profiles, explicit currency (`default_currency`, transaction snapshot, safe migration, locked change), `UNIQUE (organization_id, id, transaction_id)` on `transaction_lines`, `reopen`/`cancel` lifecycle events, generic custom-field flag filter
- [ ] Run `uv run alembic upgrade head` on the development database (migrations `d52b8f1a7c34`, `e63c9a2b8d45`) and re-run `uv run python -m app.scripts.seed_dev`
- [ ] Invoicing backend (next milestone, only after review of the prerequisites): tables, draft/issue lifecycle, counter-based numbering, immutability triggers, `reopen`/`cancel` validator on the core seam, concurrency tests

## Later
- [ ] Payments
- [ ] Pricing rules, customer-specific pricing, discounts and campaigns
- [ ] Inventory, reporting, files, audit improvements, AI

## Bugs / technical debt
- The frontend uses hand-written API types; generate them from FastAPI's OpenAPI document later.
- Dev identity (`/dev-login`, cookie) is development only; replace `lib/identity.ts` and `lib/backend.ts` with real authentication later. Behind a reverse proxy the same-origin check needs the original `Host` header forwarded.
- Frontend client: no data library yet; revisit (SWR/TanStack Query) if shared client caches or optimistic updates become real needs.
- Playwright runs against the installed Edge or Chrome (the browser download is blocked here); other browsers are not covered.
- Customers and Catalog screens have no delete (records are deactivated, since other records refer to them) and do not hide actions by role: roles are not enforced by the backend for these resources yet, and the UI would only be cosmetic anyway.
- List pages use Previous/Next (no total count) and sort by name only; there is no column sorting. The e2e specs share one seeded test database, so a spec must never rely on how many records others created (query by a unique name, never by "first N rows").
- Custom fields are only rendered for transactions and their lines. Customers, Items and Horses have no custom-field UI yet (the renderer is generic, so each is an integration like `features/transactions/TransactionFields.tsx`, and the backend must register the entity type with `custom_fields=True`). There is no definition administration UI (definitions are created through the API); list tables do not show or filter by custom values (`show_in_table` is ignored for now).
- Custom-field values are last-writer-wins: saving a record's fields sends only what changed, but two tabs editing the same field overwrite each other. The same `version` + `If-Match` pattern as Sales can be added when needed.
- A record's custom fields save together (the backend enforces required fields on every write), so a required field can never be set on its own while another required field is empty: the form saves, the backend answers 422 on the empty one.
- Custom numbers come back from the backend normalized (`0.10` is stored and shown as `0.1`); the frontend shows exactly what it receives.
- Optimistic concurrency covers Sales only. Customers, Items, Horses and custom-field values are still last-writer-wins; extend the same `version` + `If-Match` pattern when a screen needs it. Adding a line takes no version by design.
- Transaction editor V1: no change-item or detach on an existing line (the API supports both), no "delete draft" in the UI (Cancel exists), no line reordering, no autosave, and a stale line editor can only discard-and-reload (no "apply my edits on top of the latest").
- A tab that is not looked at stays stale until it is (visibility refresh only, no polling), so a long-unattended tab can show an old transaction until someone returns to it.
- The transaction date defaults to the browser's local date on create; there is still no organization time zone.
- EntityPicker V1: shows the first 20 matches (typing narrows; no "more results" hint or paging), searches on every keystroke (aborting the previous request, no debounce), has no inline "create a new customer", and its listbox has no Home/End/PageUp/PageDown keys. Reuse it for the billing customer, items and custom-field references; those need only a `search` function and a mapping to `{id, label}`.
- The horse list filters (Owner, Stable) travel in a plain GET form, so a cleared field is sent empty (`owner_customer_id=`); the server treats that as no filter.
- Forms validate only the shape of decimals locally; a message on a field that is wrong for the backend (such as too many digits) is the backend's own wording.
- Customer, Item, Horse and draft Transaction DELETE are hard deletes. Customers and Items are protected by `409` while referenced; Horse will need the same once sessions reference horses.
- Module enable/disable per organization is not built; the Equine and custom-field routes are mounted for everyone.
- Horse has no `notes` yet (deferred) and one owner only (a `horse_owners` table is the planned path to multiple ownership).
- `transaction_date` defaults to today in UTC; use the organization's timezone once organizations have settings.
- Transaction lines cannot be reordered (positions are assigned once); no discounts, no credit notes, no cash rounding.
- Custom fields: no list filtering or search by custom values, no money/percent types, one dependency per reference field, equality filters only, no definition deletion. Orphaned value rows (from deleted records) are harmless but never cleaned up.
- Custom-field values are written in a second request after a record is created (Sales does not accept them); consider a combined create flow when the frontend needs it.
- Register Items as a referenceable entity if a custom field ever needs to point at them.
- Customer email is not format-validated; the billing profile is free text by design (no jurisdiction rules).
- Consider PostgreSQL Row Level Security as defense in depth (see docs/architecture.md).
- **Currency (temporary rules).** Items have no currency of their own, so the organization's default currency cannot be changed once items or transactions exist, and a currency is a three-letter code with only its shape checked (no list of valid codes, no minor-unit handling). A real currency and repricing model replaces this. Transactions that predate currencies stay without one until an owner/admin assigns it (settings page or `POST /api/transactions/assign-currency`); there is no per-transaction assignment.
- Organization settings are last-writer-wins (no `If-Match` yet) and there is no organization time zone.
- Item `unit` is free text; no units subsystem.
- Roles are stored on memberships but not enforced anywhere yet.
- Production authentication must replace the `AUTH_MODE=dev` branch in `app/core/auth.py`.
