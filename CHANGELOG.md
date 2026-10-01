# Changelog

All notable changes to `business-platform` will be documented in this file.

## [Unreleased]

### Added
- Initial project structure: Git repository, `.gitignore`, `.env.example`, README.
- Docker Compose service for PostgreSQL.
- FastAPI backend with `/health` and `/health/db` endpoints, SQLAlchemy session setup and Alembic.
- Next.js + TypeScript + Tailwind frontend with a home page showing backend status.
- `docs/architecture.md` (domain design, split out of `CLAUDE.md`) and `docs/lessons.md`.
- `Organization` model (tenant) with Alembic migration creating the `organizations` table, plus tests using a rollback-only database session.
- `User` and `OrganizationUser` (membership with role) models and migration.
- Tenant context: the backend resolves the active organization from the current user's memberships. `X-Organization-Id` only selects among the user's own memberships; selecting any other organization returns 404.
- Development identity (`X-Dev-User-Email` / `DEV_USER_EMAIL`), isolated in `app/core/dev_identity.py`, enabled only with `AUTH_MODE=dev` and `APP_ENV=development`; the backend refuses to start with dev auth in any other environment.
- `GET /api/me` returning the current user, active organization and role.
- `python -m app.scripts.seed_dev`: idempotent development seed with two organizations, a shared multi-organization user and a single-organization user.
- Tests for membership constraints, organization selection/switching, cross-tenant selection and the seed.
- Tenant-scoped data access: `TenantOwned` model mixin (`id`, `organization_id`, timestamps) and `app/core/tenant_scope.py` helpers (`scoped_select`, `get_scoped_or_404`, `create_scoped`).
- `Customer` model (person/company) and migration, with tenant-safe CRUD and search API at `/api/customers`.
- Cross-tenant isolation tests for Customers: list, search, read, update, delete and create, using identical-looking customers in two organizations, including a multi-organization user and non-member selection.
- Development seed now includes customers (Anna Andersson in both organizations, Umeå HK in one).

### Changed
- Customer type `organization` renamed to `company`; "organization" now only means the tenant.

### Fixed

### Security
- Tenant-owned records can no longer have their `organization_id` changed after creation (ORM guard); Customer requests containing `organization_id` are rejected.
