# Changelog

All notable changes to `business-platform` will be documented in this file.

## [Unreleased]

### Added
- Customers screens (`/o/{orgId}/customers`, `/new`, `/{id}`): list with search, status filter and paging; create; edit; deactivate and reactivate. Fields: type (person/company), name, email, phone, active.
- Catalog screens (`/o/{orgId}/catalog`, `/new`, `/{id}`): list with search, type and status filters and paging; create; edit; deactivate and reactivate. Fields: type (service/product), name, description, unit, price excluding VAT, VAT rate, active. Price and VAT are decimal strings from the input to the request and back to the screen, never JavaScript numbers.
- Basic reusable frontend primitives where Customers and Catalog actually share code: labelled form controls wired to 422 errors (`Field`), `ListFilters`, `Pagination`, `StatusBadge`, `ActiveToggle`, `ErrorSummary`, a `useMutation` hook (one request at a time) and list-address helpers (`lib/list-params.ts`); `lib/server-api.ts` for server-component reads (401 to sign-in, 404 to the one generic not-found page).
- Tests for these screens: Vitest component tests for both forms (payloads, 422 mapping, changed-only updates, double submit, draft reset on organization change), list parameters, server reads and form helpers, and Playwright workflow tests (create, edit, deactivate/reactivate, search, filters, paging, history) including a full round trip of 0.10, 4.35, 8.20 and 9999999999.99 through browser, BFF, FastAPI and PostgreSQL, and tenant isolation for these screens (identical records in two organizations, search and filters, direct navigation, edits against foreign ids, switching with drafts, tabs, history and forged headers).
- Dedicated test environment: a separate `postgres-test` PostgreSQL service (own container, port 5433, disposable storage) configured only through `TEST_DATABASE_URL`. `python -m app.scripts.reset_test_db [--seed]` rebuilds it from migrations. A guard refuses any database whose name does not end in `_test` or that lives on the development server.
- Tests prove that test execution cannot modify the development database (guard refusals before any connection, and a canary row that never appears in the development database).
- `GET /api/me/organizations`: the current user's own memberships (id, name, role), needing no active organization, so a client can offer to switch.
- `?active=true|false` filter on `GET /api/customers`.
- Frontend foundations (Next.js App Router): organization-scoped routes `/o/{orgId}/...`, a BFF route handler (`/api/o/{orgId}/...`) that adds the dev identity and organization headers server-side and ignores any client-supplied ones, `/dev-login` with an httpOnly cookie (development only), the organization shell with a switcher (full navigation), a dashboard, and loading/error/not-found handling.
- Frontend libraries: structured API error mapping for 401/403/404/409/422, a browser API client scoped to an organization, decimal-string types and validators (`lib/decimal.ts`) with ESLint rules forbidding number conversion in the money-handling folders.
- Frontend tests: Vitest unit and component tests (BFF security, error mapping, decimals, organization scope reset, stale-answer handling) and Playwright end-to-end tests on the test database for organization isolation (foreign and arbitrary organization ids, switching, tabs, history, client state, forged headers, direct BFF requests, database separation).
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
- `Item` model (service or product) and migration: `type`, `name`, `description`, `unit`, `price_ex_vat` (`NUMERIC(12,2)`), `vat_rate` (`NUMERIC(5,2)`), `active`; with CHECK constraints for type, non-negative price and VAT 0-100.
- Tenant-safe Items API at `/api/items` (create, list/search with `type`/`active` filters, read, update, delete) built on the existing `TenantOwned` / `tenant_scope` infrastructure.
- Exact money handling: `app/schemas/money.py` accepts decimal strings or integers only, rejects JSON floats, exponents, `NaN`, negatives and excess decimals, and always returns two-decimal strings.
- Shared tenant-isolation contract (`tests/tenant_contract.py`, `tests/test_tenant_isolation_contract.py`) that runs the same cross-tenant checks against Customers and Items.
- Money round-trip tests (API to PostgreSQL and back) and a guard test that fails if any table uses a floating-point column.
- Development seed now includes a "Horse massage" item (850.00 excl. VAT, 25.00 % VAT) in both organizations.
- Equine domain module (`app/modules/equine/`): `Horse` model and migration with `name`, required `owner_customer_id`, optional `stable_customer_id`, `birth_year`, `sex` (mare/stallion/gelding), free-text `breed` and `active`. Owner and stable are Customer references; there is no billing field on Horse.
- Tenant-safe Horses API at `/api/horses` (create, list with `q`/`owner_customer_id`/`stable_customer_id`/`active` filters, read, update, delete) returning compact owner and stable summaries.
- Generic `resolve_reference` helper for validating ids in a request body against the active organization (identical 422 for foreign and nonexistent ids; inactive targets refused for new assignments).
- Composite foreign keys from horses to customers and `UNIQUE (organization_id, id)` on customers, so PostgreSQL itself refuses cross-tenant references.
- Generic `409` when deleting a record that other records still reference (`delete_or_409`); applied to Customers.
- Module boundary tests: the modules do not import each other and only the wiring files import a module (enforced as import rules, see below).
- Horses registered with the shared tenant-isolation contract, plus tests for references, inactive customers, deletion, filters and field validation.
- Development seed now includes the horse Kalle (owner Anna Andersson, stable Umeå HK) in Fredrik Horse Therapy and an identical-looking Kalle in the other organization.
- Sales module (`app/modules/sales/`): industry-neutral `transactions` (billing customer, date, lifecycle status) and `transaction_lines` with migration. Lines snapshot the Item's description, unit, price and VAT; quantities are `NUMERIC(12,3)`.
- Line net, VAT and gross amounts are calculated per line with half-up rounding (`pricing.py`, Decimal only), stored, and kept consistent with the inputs by PostgreSQL CHECK constraints. Transaction totals and the VAT breakdown are sums of the stored line amounts.
- Transactions API at `/api/transactions` (create with nested lines, list with `status`/`billing_customer_id`/`date_from`/`date_to` filters, read, update, delete), nested line routes, and lifecycle actions `complete`, `reopen` and `cancel`. Only drafts can be changed or deleted; every mutation locks the transaction row.
- `QuantityIn`/`QuantityOut` types extend the strict decimal-string contract to quantities (up to 3 decimals, greater than 0; output always three decimals).
- Items can no longer be deleted while a transaction line references them (generic `409`); `UNIQUE (organization_id, id)` added to `items` and `transactions` for composite foreign keys.
- Tests: pricing edge cases and parity with PostgreSQL `round`, database constraints, item-snapshot guarantees (item edits change nothing on existing lines; the same item can carry different overrides on different lines), nested-resource tenant isolation, lifecycle rules and locking, and Transactions registered with the shared tenant-isolation contract.
- Development seed now includes a completed transaction with one "Horse massage" line in both organizations.
- Core entity registry (`app/core/entity_registry.py`): modules explicitly register what they expose (custom-field targets, referenceable entities with the filters they offer, parent links, an `is_editable` callback). Registration fails fast on a misspelled column or unknown key. Customers, Horses and Sales register themselves from `main.py`.
- Core lifecycle validation seam (`app/core/lifecycle.py`): any capability can register validators, and Sales asks them before completing a transaction. A veto is a `409` with structured `problems` (record type, id, field, label).
- Core authorization helper (`app/core/authz.py`): `require_role` and `roles_required`, using the role of the active membership only.
- Custom fields (UDF) module (`app/modules/custom_fields/`) with migration: definitions (text, number, date, boolean, select, reference; required, position, enabled, `show_in_form`, `show_in_table`, `show_on_invoice`), stable-UUID select options, and one typed polymorphic value table whose composite foreign keys and CHECK constraint keep rows consistent with their definition within one organization.
- Dependent reference fields with equality filters (Owner, then Horse narrowed to that owner), configured as data from the filters modules register; choices lookup, dependency validation, and a bulk values endpoint for tables. Customer -> Project -> Work Order is proven with a synthetic module and no engine change.
- Custom-fields API at `/api/custom-fields`: entity types, definitions, options, choices and values; definition and option changes are owner/admin only.
- Required custom fields block transaction completion (on the transaction and on every line) without Sales importing Custom Fields; completed or cancelled transactions lock their custom values through the registered `is_editable` callback. Row locking is shared with Sales' lifecycle steps and proven with real concurrent connections.
- Polymorphic delete guard: `delete_or_409` consults registered reference guards, so records pointed at by custom-field references cannot be deleted. Horse DELETE now uses `delete_or_409`.
- Development seed configures Owner and Horse on transaction lines in both organizations, with values on the seeded line.
- Tests: registry rules, authorization across organizations with different roles, definition and value validation matrices, reference lifecycle (rename, deactivate, delete, dangling), completion validation, concurrency, tenant isolation (also through the shared contract, which gained a no-delete option), and the generic dependency proof.

### Changed
- Frontend decimal validators (`parseMoney`, `parsePercent`, `parseQuantity`) now check the shape only (digits with an optional decimal part). The number of digits, the 0 to 100 range and "greater than zero" are decided by the backend, whose 422 answer is shown on the field, so the frontend cannot drift from the backend rules.
- 422 messages no longer show Pydantic's "Value error, " prefix.
- Saving a new record refreshes the router so that browser Back to a list visited earlier shows the new record instead of a cached copy.
- pytest now runs against the dedicated test database instead of the development database, and rebuilds its schema from migrations at the start of every session. The frontend no longer uses `NEXT_PUBLIC_API_URL`; its server calls the backend (`BACKEND_URL`).
- Architecture tests now enforce imports and registrations instead of banning domain vocabulary from source files and docs.
- Shared helpers extracted from the Customers router into `app/core/query.py` (literal LIKE search, commit/refresh, update) and `app/api/deps.py` (pagination) so resources do not duplicate them.
- `CustomerRef` moved to the shared customer schemas (used by Horses and Transactions); the tenant contract allows resources without search; `get_scoped*` accept `for_update`.
- Tenant contract `create_body` may now be a function of the acting organization (needed for resources with references).
- Customer type `organization` renamed to `company`; "organization" now only means the tenant.

### Fixed
- Replaced the deprecated `HTTP_422_UNPROCESSABLE_ENTITY` constant with `HTTP_422_UNPROCESSABLE_CONTENT`.

### Security
- The browser never calls FastAPI and never sends identity or organization headers: the BFF builds backend requests from scratch, validates only shapes (UUID, known API areas, safe path segments, same origin, JSON bodies) and leaves membership decisions to FastAPI, which answers 404 for organizations the user does not belong to.
- Tenant-owned records can no longer have their `organization_id` changed after creation (ORM guard); Customer, Item and Horse requests containing `organization_id` are rejected. Cross-tenant references from horses to customers are rejected by both the API and PostgreSQL composite foreign keys.
