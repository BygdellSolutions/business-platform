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

## Next
- [ ] Customer list page and New Customer form (frontend)
- [ ] Phase 5: basic UDF engine (text, number, select, reference) and dependent references such as Owner -> Horse

## Later
- [ ] UDF engine
- [ ] Transactions
- [ ] Invoicing

## Bugs / technical debt
- Tests run against the dev database (rollback-only); consider a dedicated test database.
- Customer, Item and Horse DELETE are hard deletes; revisit deletion semantics (deactivate) once they are referenced by transactions/invoices/sessions. Item DELETE should use `delete_or_409` once something references Items.
- Module enable/disable per organization is not built; the Equine router is mounted for everyone.
- Horse has no `notes` yet (deferred) and one owner only (a `horse_owners` table is the planned path to multiple ownership).
- When another table references Items, add `UNIQUE (organization_id, id)` to `items` and use a composite foreign key.
- Customer fields are minimal (no billing info yet); email is not format-validated.
- Consider PostgreSQL Row Level Security as defense in depth (see docs/architecture.md).
- Currency is not modelled yet; it is planned as an Organization financial setting.
- Item `unit` is free text; no units subsystem.
- Roles are stored on memberships but not enforced anywhere yet.
- Production authentication must replace the `AUTH_MODE=dev` branch in `app/core/auth.py`.
