# CLAUDE.md — business-platform

## 1. Project identity

**Project name:** `business-platform`

`business-platform` is a multi-tenant, modular business management platform. It is intended to support many kinds of businesses rather than being tied to one industry.

The platform starts with core business functions such as:

- organizations / tenants
- users and permissions
- customers
- products and services
- pricing
- transactions
- invoicing and payments later
- configurable user-defined fields (UDFs)
- optional industry/domain modules

A horse/equine workflow is an initial real-world test case, but the core architecture MUST NOT assume that every organization works with horses.

The long-term goal is a flexible business platform where each organization enables only the modules and fields it needs.

---

## 2. How Claude Code should work on this repository

When working in this repository:

1. Read this file before making architectural decisions.
2. Read `TODO.md` before starting work.
3. Read `CHANGELOG.md` to understand what has already been implemented.
4. Inspect the existing code before creating new abstractions or files.
5. Work incrementally. Do not attempt to implement the entire platform in one change.
6. Prefer simple, maintainable implementations over premature abstraction.
7. Preserve tenant isolation in every feature.
8. Do not silently change established architecture. If a significant architectural change appears necessary, explain the reason before implementing it.
9. Update `TODO.md` and `CHANGELOG.md` as part of completed work.
10. Keep the application runnable after each meaningful development step.

When asked to implement something, first identify the smallest complete vertical slice that can be built and tested.

Example:

Instead of building the entire Customer module at once:

1. database model
2. migration
3. API endpoint
4. frontend list
5. create form
6. tenant isolation tests
7. update docs

---

## 3. Technology stack

Use the following stack unless explicitly changed by the project owner.

### Frontend

- TypeScript
- Next.js
- React
- Tailwind CSS

### Backend

- Python
- FastAPI
- SQLAlchemy
- Alembic
- Pydantic

### Database

- PostgreSQL

### Development / deployment

- Docker / Docker Compose for local services
- Git for source control
- Coolify for later staging/production deployment

Do not introduce another frontend framework, backend framework, ORM, or primary database without a strong reason and explicit approval.

---

## 4. Repository structure

Target structure:

```text
business-platform/
├── CLAUDE.md
├── README.md
├── CHANGELOG.md
├── TODO.md
├── docker-compose.yml
├── .env.example
├── .claude/skills/
├── docs/
│   ├── architecture.md
│   └── lessons.md
│
├── frontend/
│   ├── app/
│   ├── components/
│   ├── lib/
│   └── ...
│
└── backend/
    ├── alembic/
    ├── app/
    │   ├── core/
    │   ├── modules/
    │   ├── models/
    │   ├── schemas/
    │   ├── api/
    │   └── ...
    └── ...
```

The exact internal structure may evolve as real requirements emerge. Do not create large abstraction hierarchies before they are needed.

---

## 5. Architecture: modular monolith

Start as a **modular monolith**.

Use:

- one Next.js frontend
- one FastAPI backend
- one PostgreSQL database

Do NOT split the application into microservices at this stage.

Code should nevertheless be organized into modules with clear responsibilities so modules can evolve independently.

Conceptual module groups:

### Core

- organizations
- users
- authentication
- organization memberships
- roles / permissions
- audit logging
- files later
- module configuration
- UDF/custom-field engine

### Standard business modules

- customers
- catalog
- sales / transactions
- invoicing
- payments
- inventory later
- reporting / analytics later
- AI later

### Optional domain modules

Examples:

- equine
- projects
- vehicles
- properties
- work orders

Domain modules must not contaminate generic core modules with domain-specific assumptions.

For example, the Sales module must not contain a hard-coded `horse_id` simply because the first test business works with horses.

---

## 6. Multi-tenancy — NON-NEGOTIABLE

This system is multi-tenant.

An **organization** is a tenant/company using `business-platform`.

A **customer** is a person or company that the tenant does business with.

A **user** is a login identity that can belong to one or more organizations.

### Fundamental security rule

> No operation may access, reference, modify, aggregate, search, export, cache, process, or expose business data belonging to another organization.

Every tenant-owned business record must include an `organization_id` unless there is a documented architectural reason not to.

Typical record fields:

```text
id UUID
organization_id UUID
created_at
updated_at
```

Use UUIDs for externally referenced business records.

### Organization membership

Users can belong to multiple organizations through a membership table such as:

```text
organization_users
- id
- organization_id
- user_id
- role
```

Roles may initially include:

- owner
- admin
- accountant
- employee
- viewer

Do not overbuild the permission engine in V0.1.

### Backend determines tenant scope

The frontend/browser must NEVER be trusted to provide the authoritative `organization_id` for a business operation.

Expected flow:

```text
authenticated user
      ↓
active organization membership
      ↓
backend resolves organization_id
      ↓
query is scoped by organization_id
      ↓
PostgreSQL
```

A request for a valid UUID belonging to another organization should normally behave as if the resource does not exist (404), rather than revealing that another tenant owns it.

Tenant isolation applies to more than normal database queries. It must eventually cover:

- APIs
- background jobs
- search
- exports
- reports
- PDFs
- uploaded files
- caches
- analytics
- AI tools/context

### Tenant isolation tests

Tenant isolation tests are mandatory for tenant-owned modules.

A useful test pattern is:

```text
Organization A → Customer named Anna
Organization B → Customer named Anna
```

Verify that Organization A can never read, update, delete, search, reference, or otherwise discover Organization B's Anna record.

PostgreSQL Row Level Security may later be added as defense in depth, but application-level tenant scoping is still required.

---

## 7–16. Domain design

Sections 7–16 live in `docs/architecture.md`. Read the relevant section before working on that area:

- 7. Core business concepts (customers, catalog items)
- 8. Equine module as a test module
- 9. Transactions / sales must remain generic
- 10. UDF / custom field engine
- 11. Static selects vs reference fields
- 12. Dependent reference fields
- 13. Organization configuration vs user preferences
- 14. Invoice/history principle
- 15. AI is NOT phase one
- 16. Initial UI

---

## 17. Development phases

### Phase 0 — repository/bootstrap

Create and verify:

- repository structure
- Git
- `.gitignore`
- `.env.example`
- Docker Compose
- PostgreSQL
- FastAPI startup
- Next.js startup
- basic README
- `TODO.md`
- `CHANGELOG.md`

Success condition:

```text
frontend runs
backend runs
PostgreSQL runs
backend can connect to PostgreSQL
```

### Phase 1 — tenant foundation

Implement the minimum needed for:

- organizations
- users/memberships or a temporary development identity strategy
- tenant context
- tenant-scoped queries
- tenant isolation tests

Do not postpone tenant isolation until after all CRUD modules are built.

### Phase 2 — Customers

Implement a complete small vertical slice:

- Customer SQLAlchemy model
- Alembic migration
- Pydantic schemas
- tenant-safe CRUD API
- Customer list page
- New Customer form
- edit/view as needed
- tests

Success condition:

A customer entered in the browser persists in PostgreSQL and appears after restarting/reopening the application.

### Phase 3 — Catalog

Implement Products/Services using generic Items.

Start with:

- service/product type
- name
- unit
- price
- VAT
- active

### Phase 4 — Equine test module

Implement Horse records with owner references.

Success example:

```text
Customer: Anna Andersson
Customer: Umeå HK
Horse: Kalle
Owner: Anna Andersson
Stable: Umeå HK
```

### Phase 5 — basic UDF engine

Only after normal CRUD and tenant scoping work reliably.

Implement initial field types:

- text
- number
- select
- reference

Then test dependent references:

```text
Owner → Horse
```

### Phase 6 — Transactions

Build a generic transaction workflow.

Example:

```text
Billing customer: Umeå HK
Owner: Anna
Horse: Kalle
Item: Massage
Qty: 1
Price: 850
```

Store domain context generically rather than hard-coding Horse into Sales.

### Later phases

Only after the foundation is stable:

- pricing rules
- customer-specific pricing
- discounts/campaigns
- pending invoicing
- invoice generation
- invoice PDFs
- payments
- inventory
- reporting
- audit improvements
- files
- AI
- specialized domain workflows

---

## 18. What NOT to build yet

Unless explicitly requested, avoid spending early development time on:

- microservices
- Kubernetes
- complex event buses
- a full plugin marketplace/framework
- elaborate RBAC engines
- AI agents
- perfect invoice PDFs
- Stripe/Swish integrations
- advanced analytics
- mobile apps
- premature caching
- premature performance optimization

Build the boring, reliable foundation first.

---

## 19. Database changes

Database schema must be managed through:

```text
SQLAlchemy models
        ↓
Alembic migrations
        ↓
PostgreSQL
```

Do not manually edit production database tables as the normal development workflow.

Every schema change should have an Alembic migration.

Never casually delete production data or recreate the database to solve a migration problem.

---

## 20. API conventions

Use FastAPI REST endpoints initially.

Prefer predictable module routes such as:

```text
/api/customers
/api/items
/api/horses
```

Use Pydantic request/response schemas.

Validate references on the backend.

For example, when assigning a horse owner:

1. resolve current organization
2. query Customer using BOTH customer UUID and organization_id
3. reject if not found in current tenant
4. only then save the relationship

Never trust the frontend to prove that a referenced UUID belongs to the active organization.

---

## 21. Error handling

Errors should be useful to developers without exposing sensitive tenant information to users.

Do not reveal that a requested UUID belongs to another organization.

Prefer consistent API error structures.

Log enough server-side context to diagnose problems, but do not log passwords, secrets, tokens, or unnecessary sensitive business data.

---

## 22. Security basics

At minimum:

- secrets belong in environment variables
- commit `.env.example`, not real `.env` secrets
- never commit passwords/API keys/tokens
- validate backend inputs
- enforce permissions server-side
- enforce tenant isolation server-side
- avoid raw SQL unless justified and parameterized
- do not expose PostgreSQL publicly in production

Authentication details can evolve, but security must not depend on hiding UI controls.

---

## 23. Testing priorities

Tests should focus first on behavior that would be expensive or dangerous to break.

Priority order:

1. tenant isolation
2. permissions/security
3. financial calculations when introduced
4. cross-module references
5. API CRUD behavior
6. important frontend flows

For every tenant-owned resource, consider tests for:

```text
create
list
read
update
delete
cross-tenant read
cross-tenant update
cross-tenant delete
cross-tenant reference assignment
```

---

## 24. Code quality rules

Prefer:

- clear names
- small functions
- explicit behavior
- typed interfaces/schemas
- straightforward SQLAlchemy queries
- reusable components when actual duplication exists

Avoid:

- giant files
- unexplained magic
- unnecessary metaprogramming
- abstraction merely for abstraction's sake
- generic "enterprise" patterns before requirements exist

Comments should explain **why**, not restate obvious code.

---

# Project documentation workflow

## 25. TODO.md — source of upcoming work

`TODO.md` is the project's short-term work queue and planning document.

Claude Code MUST read it before starting a development task.

Keep it useful rather than turning it into a dump of every future idea.

Recommended structure:

```markdown
# TODO — business-platform

## Now
- [ ] Bootstrap PostgreSQL in Docker Compose
- [ ] Create FastAPI `/health` endpoint
- [ ] Verify backend database connection

## Next
- [ ] Create Organization model
- [ ] Add tenant context
- [ ] Create Customer model and migration

## Later
- [ ] Catalog module
- [ ] Equine module
- [ ] UDF engine
- [ ] Transactions
- [ ] Invoicing

## Bugs / technical debt
- None yet
```

### TODO rules

When starting work:

1. Read `TODO.md`.
2. Identify the task being worked on.
3. Do not automatically implement unrelated TODO items.

When completing work:

1. mark completed items `[x]`, OR remove/archive them if the file becomes noisy
2. add newly discovered concrete follow-up work
3. move the next logical tasks into `Now` when appropriate
4. do not mark something complete unless it actually works

Do not use TODO.md as a changelog.

`TODO.md` answers:

> What should we work on next?

---

## 26. CHANGELOG.md — record of completed changes

`CHANGELOG.md` records meaningful completed changes to the project.

It answers:

> What changed?

Use a simple Keep-a-Changelog-inspired structure.

Recommended initial format:

```markdown
# Changelog

All notable changes to `business-platform` will be documented in this file.

## [Unreleased]

### Added
- Initial project structure.

### Changed

### Fixed

### Security
```

Use categories when relevant:

```text
Added
Changed
Fixed
Removed
Security
```

### Changelog rules

After completing a meaningful change, update `[Unreleased]`.

Examples:

```markdown
### Added
- Added tenant-scoped Customer CRUD API.
- Added Customer list and create screens.
- Added tenant isolation tests for Customer records.

### Fixed
- Prevented horse owner references from targeting customers in another organization.

### Security
- Added organization scoping to Item lookup endpoints.
```

Do NOT add noisy entries such as:

```text
- Renamed local variable x.
- Fixed typo in comment.
- Ran formatter.
```

unless the change is genuinely important to users/developers.

When a release/version is eventually created, move relevant `[Unreleased]` entries into a dated version section, for example:

```markdown
## [0.1.0] - 2026-10-15
```

Do not invent release versions without being asked or without an established release process.

---

## 27. Relationship between TODO and CHANGELOG

Use this mental model:

```text
TODO.md
"We need to build Customer creation."
        ↓
implementation
        ↓
tests
        ↓
working feature
        ↓
CHANGELOG.md
"Added tenant-scoped Customer creation."
```

`TODO.md` describes intended work.

`CHANGELOG.md` describes completed meaningful work.

Keep them synchronized, but do not duplicate entire descriptions between them.

---

## 28. End-of-task checklist for Claude Code

Before declaring an implementation task complete, check:

```text
[ ] Does the code compile/run?
[ ] Were relevant tests run?
[ ] Is tenant isolation preserved?
[ ] Are references validated inside the active organization?
[ ] Is a migration included for schema changes?
[ ] Were secrets kept out of source control?
[ ] Is TODO.md updated?
[ ] Is CHANGELOG.md updated for meaningful changes?
[ ] Is README/docs updated if setup or usage changed?
```

If tests cannot be run, state that clearly rather than pretending they passed.

---

## 29. Git workflow

Keep commits focused and understandable.

Example commit messages:

```text
chore: bootstrap backend and postgres
feat: add tenant-scoped customers
feat: add catalog items
feat: add horse owner references
feat: add custom field definitions
fix: enforce tenant scope on customer lookup
```

Do not commit generated secrets, `.env`, database dumps, dependency caches, or editor-specific junk.

Do not rewrite shared Git history unless explicitly instructed.

---

## 30. First milestone

The first milestone is deliberately small.

A developer should be able to:

1. start the project locally
2. open the frontend in a browser
3. create or use a development organization
4. create a Customer
5. refresh/restart and still see that Customer
6. verify another organization cannot access that Customer

Then add:

7. Products/Services
8. Horses with Customer owner references

Example milestone data:

```text
Organization:
Fredrik Horse Therapy

Customers:
Anna Andersson (Person)
Umeå HK (Company)

Horse:
Kalle
Owner: Anna Andersson
Stable: Umeå HK

Item:
Horse massage
Type: Service
Price: 850 SEK
VAT: 25%
```

The milestone is complete when this data is persisted correctly and tenant isolation is tested.

Do not require UDFs, invoicing, payments, or AI for this milestone.

---

## 31. First action in a brand-new repository

If this repository contains little or no implementation yet, do not immediately generate dozens of modules.

Start by inspecting the repository and then build Phase 0.

Suggested order:

```text
1. Initialize/inspect Git repository
2. Create README.md
3. Create TODO.md
4. Create CHANGELOG.md
5. Create .gitignore
6. Create .env.example
7. Create Docker Compose with PostgreSQL
8. Bootstrap FastAPI backend
9. Add /health endpoint
10. Connect FastAPI to PostgreSQL
11. Bootstrap Next.js + TypeScript + Tailwind frontend
12. Confirm frontend and backend both run
13. Update TODO.md and CHANGELOG.md
```

Stop and fix problems as they appear rather than layering more code on a broken foundation.

---

## 32. Guiding principles

When uncertain, prefer these principles:

### General before specialized

Build generic Customers, Items, Transactions and custom references. Keep Horse-specific logic in the Equine module.

### Secure by construction

Tenant scope should be difficult to accidentally bypass.

### Configuration instead of forks

Organizations should customize fields/modules without requiring separate copies of the application.

### Real entities when behavior becomes real

A simple value can start as a UDF. Once something has relationships, history, workflows, documents, or business logic, consider promoting it to a proper module/entity.

Example:

```text
"Horse name" text field
        ↓ requirements grow
Horse entity with UUID, owner, history, treatments, documents
```

### Simple first, extensible underneath

The UI can initially expose only a few custom fields or options, while the underlying architecture avoids unnecessary hard limits.

### Backend owns business rules

The frontend provides the experience. FastAPI enforces authorization, tenant isolation, validation, and business rules. PostgreSQL is the authoritative data store.

### Build vertical slices

Prefer a small feature that works from browser to database over many half-finished layers.

---

## 33. Instruction to Claude Code

When beginning work on `business-platform`, first:

1. inspect the repository
2. read `CLAUDE.md`
3. read `TODO.md` and `CHANGELOG.md` if they exist
4. identify the current phase
5. explain the next small implementation step
6. implement only the agreed/current scope
7. run appropriate checks/tests
8. update TODO and CHANGELOG

Do not assume that a future feature listed in this document should be implemented immediately.

The goal is to evolve `business-platform` carefully from a small working application into a flexible multi-tenant modular business platform.

---

## 34. Working rules

- Plan first for any non-trivial task (3+ steps or an architectural decision), and re-plan if something goes sideways.
- Use subagents for research, exploration and parallel analysis to keep the main context clean.
- After any correction from the project owner, add the pattern to `docs/lessons.md` and review that file at session start.
- Commit messages state what changed and why. For bug fixes, note the before/after behavior.
- Check in before anything costly or risky: a paid API call, a destructive Git operation, a release/publish step.
