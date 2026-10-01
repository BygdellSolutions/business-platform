# TODO — business-platform

## Now
- [x] Install Docker Desktop and run `docker compose up -d`
- [x] Verify `/health/db` returns `{"database":"ok"}` (backend can connect to PostgreSQL)
- [x] Set Git `user.name` / `user.email` and make the first commit
- [x] Verify frontend runs (`npm run dev`) and reaches the backend
- [x] Create Organization model and migration (start of Phase 1)
- [x] Users, organization memberships and development identity (`/api/me`, tenant context)
- [x] Tenant-scoped query helpers, Customer model/migration, minimal tenant-safe Customer API, cross-tenant tests

## Next
- [ ] Customer list page and New Customer form (frontend)
- [ ] Phase 3: Catalog (generic Items), reusing `TenantOwned` and `tenant_scope`

## Later
- [ ] Equine module
- [ ] UDF engine
- [ ] Transactions
- [ ] Invoicing

## Bugs / technical debt
- Tests run against the dev database (rollback-only); consider a dedicated test database.
- Customer DELETE is a hard delete; revisit (deactivate instead) once other records reference customers.
- Customer fields are minimal (no billing info yet); email is not format-validated.
- Consider PostgreSQL Row Level Security as defense in depth (see docs/architecture.md).
- Roles are stored on memberships but not enforced anywhere yet.
- Production authentication must replace the `AUTH_MODE=dev` branch in `app/core/auth.py`.
