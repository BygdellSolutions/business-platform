# TODO — business-platform

## Now
- [x] Install Docker Desktop and run `docker compose up -d`
- [x] Verify `/health/db` returns `{"database":"ok"}` (backend can connect to PostgreSQL)
- [x] Set Git `user.name` / `user.email` and make the first commit
- [x] Verify frontend runs (`npm run dev`) and reaches the backend
- [x] Create Organization model and migration (start of Phase 1)
- [x] Users, organization memberships and development identity (`/api/me`, tenant context)

## Next
- [ ] Add tenant-scoped query helpers for tenant-owned tables (first user: Customer)
- [ ] Tenant isolation tests
- [ ] Create Customer model and migration

## Later
- [ ] Catalog module
- [ ] Equine module
- [ ] UDF engine
- [ ] Transactions
- [ ] Invoicing

## Bugs / technical debt
- Tests run against the dev database (rollback-only); consider a dedicated test database.
- Roles are stored on memberships but not enforced anywhere yet.
- Production authentication must replace the `AUTH_MODE=dev` branch in `app/core/auth.py`.
