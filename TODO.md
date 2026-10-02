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
- [ ] Frontend slice 2: Customers and Catalog (list, create, edit) with the shared UI primitives
- [ ] Frontend slice 3: Horses with Owner and Stable pickers
- [ ] Frontend slice 4: Transactions (list, create-then-edit, line editor, backend-owned totals, lifecycle)
- [ ] Frontend slice 5: generic custom-field renderer, dependent references, completion problems mapped to lines, read-only completed transactions
- [ ] Invoicing design: invoice state as its own relationship to transactions (not a lifecycle status); snapshot at issuance

## Later
- [ ] Payments
- [ ] Pricing rules, customer-specific pricing, discounts and campaigns
- [ ] Inventory, reporting, files, audit improvements, AI

## Bugs / technical debt
- The frontend uses hand-written API types; generate them from FastAPI's OpenAPI document later.
- Dev identity (`/dev-login`, cookie) is development only; replace `lib/identity.ts` and `lib/backend.ts` with real authentication later. Behind a reverse proxy the same-origin check needs the original `Host` header forwarded.
- Frontend client: no data library yet; revisit (SWR/TanStack Query) if shared client caches or optimistic updates become real needs.
- Playwright runs against the installed Edge or Chrome (the browser download is blocked here); other browsers are not covered.
- Customer, Item, Horse and draft Transaction DELETE are hard deletes. Customers and Items are protected by `409` while referenced; Horse will need the same once sessions reference horses.
- Module enable/disable per organization is not built; the Equine and custom-field routes are mounted for everyone.
- Horse has no `notes` yet (deferred) and one owner only (a `horse_owners` table is the planned path to multiple ownership).
- `transaction_date` defaults to today in UTC; use the organization's timezone once organizations have settings.
- Transaction lines cannot be reordered (positions are assigned once); no discounts, no credit notes, no cash rounding.
- Custom fields: no list filtering or search by custom values, no money/percent types, one dependency per reference field, equality filters only, no definition deletion. Orphaned value rows (from deleted records) are harmless but never cleaned up.
- Custom-field values are written in a second request after a record is created (Sales does not accept them); consider a combined create flow when the frontend needs it.
- Register Items as a referenceable entity if a custom field ever needs to point at them.
- Add `UNIQUE (organization_id, id)` to `transaction_lines` when invoice lines reference them.
- Customer fields are minimal (no billing info yet); email is not format-validated.
- Consider PostgreSQL Row Level Security as defense in depth (see docs/architecture.md).
- Currency is not modelled yet; it is planned as an Organization financial setting.
- Item `unit` is free text; no units subsystem.
- Roles are stored on memberships but not enforced anywhere yet.
- Production authentication must replace the `AUTH_MODE=dev` branch in `app/core/auth.py`.
