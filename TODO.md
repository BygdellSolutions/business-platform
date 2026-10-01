# TODO — business-platform

## Now
- [x] Install Docker Desktop and run `docker compose up -d`
- [x] Verify `/health/db` returns `{"database":"ok"}` (backend can connect to PostgreSQL)
- [x] Set Git `user.name` / `user.email` and make the first commit
- [x] Verify frontend runs (`npm run dev`) and reaches the backend
- [ ] Create Organization model and migration (start of Phase 1)

## Next
- [ ] Users / memberships, or a temporary development identity strategy
- [ ] Add tenant context and tenant-scoped queries
- [ ] Tenant isolation tests
- [ ] Create Customer model and migration

## Later
- [ ] Catalog module
- [ ] Equine module
- [ ] UDF engine
- [ ] Transactions
- [ ] Invoicing

## Bugs / technical debt
- None yet
