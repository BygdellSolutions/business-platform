# Architecture and domain design — business-platform

Domain design reference for `business-platform`, split out of `CLAUDE.md` (sections 7–16). Read the relevant section before working on that area.

---

## 7. Core business concepts

### Customers

A customer can be either:

```text
person
company
```

The customer type is called `company`, not `organization`, because "organization" always means the tenant in this project.

Example conceptual model:

```text
customers
- id
- organization_id
- customer_type
- name
- email
- phone
- billing information
- active
- created_at
- updated_at
```

Do not create a separate generic `owners` table merely because the equine module uses owners.

"Owner" can be a role played by a Customer/Person record.

### Catalog / warehouse

Use a generic `items` concept for both services and products.

Example:

```text
items
- id
- organization_id
- type            # service | product
- name
- description
- unit            # free text for now ("hour", "session", "pcs")
- price_ex_vat    # NUMERIC(12,2), excludes VAT; no gross price is stored
- vat_rate        # NUMERIC(5,2) percent, 0-100
- active
- created_at
- updated_at
```

The UI may call this area **Catalog**, **Products & Services**, or **Warehouse**, while the internal model remains generic.

Do not create separate architectures for products and services unless their behavior later requires it.

---

## 8. Equine module as a test module

The equine module is optional domain functionality.

It exists initially because it provides a useful real-world test of relationships and dynamic fields.

Example:

```text
horses
- id
- organization_id
- name
- owner_customer_id        # required, Customer of the SAME organization
- stable_customer_id       # optional, Customer of the SAME organization
- birth_year (optional)    # integer 1900..current year; no age calculation
- sex (optional)           # mare | stallion | gelding (CHECK constraint, no lookup table)
- breed (optional)         # free text
- active
- created_at
- updated_at
```

Implemented in `app/modules/equine/`. `notes` is deferred.

`owner_customer_id` references a Customer record belonging to the SAME organization.

A horse is NOT a customer.

A horse's owner and the party paying an invoice do not have to be the same entity.

Example:

```text
Billing customer: Umeå HK
Owner:            Anna Andersson
Horse:            Kalle
Item:             Massage
Quantity:         1
```

Umeå HK can pay even though Anna owns Kalle.

Therefore billing relationships and domain relationships must remain separate.

---

## 9. Transactions / sales must remain generic

A generic sales/transaction line should eventually contain concepts such as:

```text
customer
item
quantity
unit price
discount
VAT
totals
```

It must NOT contain domain-specific columns such as:

```text
horse_id
vehicle_id
property_id
```

Domain-specific context should be attached through configurable references/custom fields or another generic relationship mechanism.

This allows the same Sales module to support:

```text
Equine business:
Customer → Owner → Horse → Massage

Consultancy:
Customer → Project → Consulting hours

Workshop:
Customer → Vehicle → Repair

Property company:
Customer → Property → Service
```

---

## 10. UDF / custom field engine

UDF means **User-Defined Field** / custom field.

The UDF engine is a Core capability and an important part of this platform.

Organizations should be able to customize records/forms without requiring source-code changes.

### Important design rule

The architecture should support an arbitrary number of custom fields even if the first UI intentionally limits how many can be created or displayed.

Do NOT implement fixed database columns such as:

```text
udf_text_1
udf_text_2
udf_text_3
```

as the permanent UDF architecture.

Instead use definitions/configuration and stored values.

Conceptually:

```text
custom_field_definitions
- id
- organization_id
- entity_type
- key
- label
- field_type
- position
- required
- enabled
- configuration
- created_at
- updated_at
```

Values should be associated with a definition and a tenant-owned entity. The exact value-storage implementation should be chosen deliberately when this feature is implemented.

### Initial field types

Do NOT implement every possible UDF type immediately.

Start with:

```text
text
number
select
reference
```

Possible later types:

```text
long_text
decimal
currency
date
datetime
boolean
multi_select
customer_reference
entity_reference
```

### Hidden by default

Custom fields should not clutter the normal UI.

An organization administrator enables/configures them in a customization/settings screen.

An enabled field should appear like a normal/native field in forms and tables. Avoid forcing users to interact with a visibly separate "Custom Fields" box unless there is a UX reason.

Configurable properties may eventually include:

- label
- enabled
- required
- position
- show on create form
- show on edit form
- show in table
- show in search
- show on invoice
- column width

Core fields may also support configurable display labels, but required system fields must not be removable if doing so would break data integrity.

---

## 11. Static selects vs reference fields

These are different field types.

### Static select

Options are configured manually.

Example:

```text
Priority
- Low
- Normal
- High
```

### Reference field

Options come from actual records in another module.

Example:

```text
Owner
Type: reference
Source: customers
Display: customer.name
```

The stored value should be the referenced record's UUID, not merely its displayed text.

---

## 12. Dependent reference fields

Reference fields should eventually support dependencies.

Example transaction form:

```text
Billing customer  [ Umeå HK ▼ ]
Owner             [ Anna Andersson ▼ ]
Horse             [ Kalle ▼ ]
Item              [ Massage ▼ ]
Quantity          [ 1 ]
```

Configuration:

```text
Owner
- type: reference
- source: customers

Horse
- type: reference
- source: horses
- depends_on: Owner
- relationship/filter: horse.owner_customer_id = selected Owner
```

Selecting Anna causes the Horse dropdown to show only horses related to Anna.

The mechanism should be generic enough to later support:

```text
Customer → Project
Customer → Vehicle
Owner → Horse
Customer → Property
Project → Work Order
```

Do not implement a special `owner_horse_dropdown` component. Implement generic reference/dependency behavior when this phase is reached.

Also remember that billing customer and owner may differ. Never automatically assume:

```text
billing_customer == owner
```

---

## 13. Organization configuration vs user preferences

Keep these concepts separate.

### Organization configuration

Defines what the tenant uses.

Examples:

- enabled modules
- enabled custom fields
- field labels
- required fields
- business rules
- invoice configuration

### User preferences

Defines how an individual user prefers to view the enabled functionality.

Examples later:

- table column visibility
- column order
- saved filters
- dashboard layout

Conceptually:

```text
MODULE
    ↓
ORGANIZATION CONFIGURATION
    ↓
USER PREFERENCE
```

If an organization does not use a field or module, normal users should not have to look at it.

---

## 14. Invoice/history principle

When invoicing is implemented, issued invoices must preserve historical values.

Do not render historical invoices entirely from mutable live Customer/Item records.

Invoice data should snapshot the relevant values at issuance, such as:

- customer name/address
- item description
- unit price
- quantity
- discount
- VAT
- custom context displayed on the invoice

Changing a Customer or Item later must not silently change an already-issued invoice.

---

## 15. AI is NOT phase one

The platform may later include AI-assisted operations, but do not build AI into V0.1.

Long-term principle:

```text
AI understands user intent
        ↓
FastAPI determines allowed operations
        ↓
Python business logic performs calculations/actions
        ↓
PostgreSQL remains authoritative
```

AI must not become the source of truth for prices, invoice totals, permissions, tenant isolation, or accounting state.

---

## 16. Initial UI

The first useful navigation can be approximately:

```text
Dashboard
Customers
Catalog
Horses
Organization
Settings
```

This is not a permanent navigation specification.

The goal is to get a usable interface quickly.

### First Customers screen

Aim for something simple:

```text
Customers

[ Search... ]                     [ + New customer ]

Anna Andersson        Person
Erik Svensson         Person
Umeå HK               Company
```

Do not spend excessive time on visual perfection before the CRUD flow works end to end.

---

## Implementation notes: tenant-scoped data access

How tenant isolation (CLAUDE.md section 6) is enforced in code. Follow this for every tenant-owned table.

- **Model:** inherit `TenantOwned` (`app/models/mixins.py`). It provides `id`, a NOT NULL `organization_id` foreign key (indexed) and timestamps. It also refuses any flush that changes `organization_id` on an existing record.
- **Reads and writes:** go through `app/core/tenant_scope.py` — `scoped_select`, `get_scoped`, `get_scoped_or_404`, `create_scoped` — which take the organization from the `TenantContext` produced by `get_tenant_context`. Do not start a query from a bare `select(Model)` in an endpoint.
- **Schemas:** request schemas have no `organization_id` field and use `extra="forbid"`, so a client-supplied value is a 422. Response schemas do not expose it.
- **Foreign ids:** a record from another organization behaves as nonexistent (404, same body as a random UUID).
- **Tests:** each tenant-owned resource needs cross-tenant list, search, read, update, delete and create tests with identical-looking data in two organizations (see `backend/tests/test_customers_isolation.py`).
- **Deferred:** PostgreSQL Row Level Security as defense in depth; bulk `UPDATE`/`DELETE` statements bypass the ORM guard, so they must also start from `scoped_select`-style filters.

---

## Implementation notes: money and VAT

- **Exact types only.** Money and percentages are PostgreSQL `NUMERIC` and Python `Decimal`. No float/real/double column may exist; `tests/test_item_money.py` fails if one is added to any table.
- **Item price is net.** `price_ex_vat` excludes VAT and `vat_rate` is a percentage. Gross/net/VAT amounts, rounding policy and discounts belong to the later pricing/transaction layer, not to Item.
- **Items are current state.** Transactions must copy name, price and VAT at the time of sale so that editing an Item never changes history (section 14).
- **Currency** is not on Item. It will be an Organization financial setting.
- **API input.** Money/percent values are sent as a decimal string (`"19.99"`) or an integer. JSON numbers with decimals are rejected because the client's number has already been parsed into a binary float. The pattern also rejects exponents, signs, spaces, `NaN`/`Infinity`, and more than 2 decimals (never silently rounded). Shared types: `app/schemas/money.py`.
- **API output.** Always a string with two decimals (`"850.00"`, `"25.00"`).

---

## Implementation notes: domain modules and references

**Module-defined fields vs UDFs (architectural rule).** Fields a domain module defines are the normal, stable attributes of that domain entity (a horse's birth year, sex and breed). They are real columns with validation and do not require UDF configuration. UDFs extend an entity with organization-specific fields; they never replace normal domain modeling.

**Boundary.** A domain module (`app/modules/<name>/`) may import core and the generic modules (customers, catalog). Nothing else may import it. Only `app/main.py` (mounts the router and registers it), `alembic/env.py` (imports the models) and the dev seed know a module exists. `tests/test_module_boundaries.py` enforces these import rules (not vocabulary: ordinary domain words may appear anywhere). There is no `customer.horses`; use `GET /api/horses?owner_customer_id=...`. Per-organization enabling of modules is not built yet.

**Tenant-safe references (three layers).**
1. API: `resolve_reference(db, ctx, Model, id, field)` (`app/core/tenant_scope.py`) checks the id exists in the active organization. A foreign id and a nonexistent id give the same 422 on the field (`reference.not_found`), so references cannot probe other tenants. Inactive records are refused for new assignments (`reference.inactive`); an unchanged existing reference stays valid after the target is deactivated.
2. Database: composite foreign keys `(organization_id, <ref>_id) -> target(organization_id, id)`, which need `UNIQUE (organization_id, id)` on the target table (added to `customers`; add it to any table that other tables will reference). The database itself refuses a cross-tenant link.
3. Tests: the shared tenant contract plus module-specific reference tests, including direct SQL that bypasses the API.

**Deleting referenced records.** References use `ON DELETE RESTRICT`. `delete_or_409` (`app/core/query.py`) turns the foreign-key violation into `409` with a generic message that does not name the referencing module. Deactivate (`active=false`) instead.

**Owner, stable and billing are separate.** `Horse.owner_customer_id` and `Horse.stable_customer_id` are independent Customer references; neither implies the other. The billing customer belongs to the future transaction, never to the horse.

**Owner -> Horse filtering later (section 12).** The horse list already filters by `owner_customer_id` and `stable_customer_id`. A future UDF reference field is configuration data (source `horses`, `depends_on` owner, filter parameter `owner_customer_id`); Sales and the UDF engine need no horse-specific code. When UDFs arrive, modules register their reference sources in a small registry. Polymorphic references cannot use composite foreign keys, so they are validated with `resolve_reference` instead.

---

## Implementation notes: sales / transactions

Implemented in `app/modules/sales/`. Sales is industry-neutral: it imports core, Customers and Catalog only, never a domain module or the custom-fields module, and nothing but the wiring files may import Sales (both directions are enforced as import rules by `tests/test_module_boundaries.py`).

**Header and lines.** `transactions` (billing customer, date, status) and `transaction_lines`. The **billing customer is on the header**, one per transaction, required, and never inferred from or assumed equal to any other customer. A transaction has any number of lines; a draft may have none, but completing needs at least one.

**Item reference and snapshot.** A line stores `item_id` (nullable: ad-hoc lines) only as a link. It also copies description, unit, unit price (ex VAT) and VAT rate when created; later Item edits never change them (section 14). The Item's values are defaults the request may override per line; changing a line's `item_id` re-copies the new item's values for fields not overridden in the same request. Inactive items are refused for new assignments only.

**Quantity** is `NUMERIC(12,3)` and strictly positive, sent as a decimal string like money. Credits and refunds will be separate documents, not negative lines.

**Calculation** (`pricing.py`, Decimal only):
```text
net   = round_half_up(quantity * unit_price_ex_vat, 2)
vat   = round_half_up(net * vat_rate / 100, 2)
gross = net + vat
```
Each line is rounded on its own and the three amounts are **stored** on the line; PostgreSQL CHECK constraints (`round(numeric, 2)` rounds half away from zero, equal to half-up for these non-negative values) keep them consistent with the inputs. **Header totals and the VAT breakdown are sums of the stored line amounts, grouped by rate.** They are never recomputed from grouped net totals, so lines always add up. A line net above 9,999,999,999.99 is rejected, never truncated. No cash rounding yet.

**Lifecycle status** (`draft`, `completed`, `cancelled`) describes the transaction itself and nothing else. `completed` means finalized and ready for future invoicing; it is *not* defined as "uninvoiced". When invoicing is built, invoice state is modeled as its own relationship (an invoice referencing transactions or lines), not as another lifecycle status. Transitions use action endpoints (`complete`, `reopen`, `cancel`); `status` is never accepted in a request body.

**Editing and deleting.** Header and lines change only while `draft` (409 otherwise; a completed transaction must be reopened first). Only drafts can be deleted, and their lines go with them; completed or cancelled transactions are kept (cancel instead). Every mutation locks the transaction row (`SELECT ... FOR UPDATE`) before checking the status, so a line edit cannot race a completion. Customers and Items referenced by a transaction cannot be deleted (generic `409`, deactivate instead).

**Tenant-safe references.** Composite foreign keys: header to customer, line to header (ON DELETE CASCADE), line to item; each target has `UNIQUE (organization_id, id)`. The API validates ids with `resolve_reference` (identical 422 for foreign and nonexistent ids), and nested routes match both the organization and the transaction in the path.

**Future custom fields (UDFs) without Sales knowing the referenced module.** Custom-field definitions will name an `entity_type` such as `transaction` or `transaction_line`; values are stored against `(organization_id, definition_id, entity_type, entity_id)`. Modules register their entity types (and reference sources) at startup in a small registry, so the UDF engine validates entity and reference ids through the registry and `resolve_reference` without importing Sales or any domain module. Whether a field lives on the header or on each line is the field definition's choice. Sales will register a small "is this entity editable" check so a completed transaction locks its lines' custom values without the UDF engine learning what `completed` means. Known gap: polymorphic references cannot use composite foreign keys, so deleting a referenced record will need a registered "is referenced" check.

**Invoicing later.** Creating an invoice will lock the completed transactions of one billing customer and copy, not recalculate: customer name and address, each line's description, unit, quantity, price, VAT rate and the three stored amounts, plus a text snapshot of the resolved custom-field context. Invoice lines will reference their source transaction line with a restrictive composite foreign key. Corrections will use credit notes. Partial invoicing of lines within one transaction is out of scope.

---

## Implementation notes: core registry and lifecycle seam

Generic capabilities and modules never import each other. They meet in **core**:

```text
customers, catalog, sales, domain modules ──► core registry ◄── custom fields (and future capabilities)
```

**Entity registry (`app/core/entity_registry.py`).** Each module calls `register(registry)` explicitly from `main.py` and says what it exposes: `EntityType(key, label, model)` plus optionally `custom_fields` (organizations may add fields to it), `reference` (other records may point at it: label/search/active columns and `FilterSpec`s, meaning "column X of this entity points at entity type Y"), `parent` (belongs to another entity type through a column) and `is_editable` (a callback answering "may this record's custom values change now?"). Registration fails fast on a misspelled column, a duplicate key or an unknown entity key (`registry.validate()` at startup). `registry.isolated()` lets a test register extras without leaving a trace.

**Lifecycle validation (`app/core/lifecycle.py`).** A module that owns a lifecycle step calls `ensure_valid(db, ctx, event, entity_key, entity_id)` before performing it (Sales does this for `complete`). Anything registered on the registry may veto by returning `Problem`s. A veto is a 409 with `{code: "validation_failed", event, message, total, problems: [{code, message, entity_type, entity_id, field, label}]}`, enough for a frontend to locate the record and field. Validators receive the `TenantContext` and only look at that organization.

**Authorization (`app/core/authz.py`).** `require_role(ctx, allowed_roles)` and the dependency factory `roles_required(*roles)`. The role is always the one in the ACTIVE membership, so a user who is an owner in one organization and a viewer in another gets the right answer in each; selecting an organization you do not belong to is still a 404.

**Polymorphic delete guard.** `delete_or_409` also asks registered reference guards, so a record pointed at from somewhere a foreign key cannot express (a custom-field reference) cannot be deleted.

---

## Implementation notes: custom fields (UDFs)

Implemented in `app/modules/custom_fields/`. It depends on core only and never names a concrete entity: everything it knows about entities comes from the registry. Attributes that are normal and stable for an entity stay real columns in that entity's module; custom fields extend an entity with organization-specific fields and never replace normal domain modeling.

**Definitions** (`custom_field_definitions`, tenant-owned): `entity_type` (registry key), `key` (slug, unique per organization and entity type), `label` (organization-defined), `field_type` (`text`, `number`, `date`, `boolean`, `select`, `reference`), `required`, `position`, `enabled`, `show_in_form`, `show_in_table`, `show_on_invoice`, and for references a `reference_source` plus an optional single dependency (`depends_on_definition_id`, `depends_on_filter`). Structural properties (`entity_type`, `key`, `field_type`, `reference_source`, the dependency) are immutable; label, required, position, enabled and the `show_*` flags can change. Definitions are disabled, never deleted. A field with enabled dependents cannot be disabled, and a dependent field cannot be enabled while its parent is disabled. Money and percent field types are deferred until currency exists.

**`show_on_invoice`** means the field is *eligible to be snapshotted* when an invoice is created. A future invoice must store the rendered label and value it needs at issuance; it must never resolve current definitions or reference targets to display an existing invoice.

**Options** (`custom_field_options`): the choices of a select field, each with a stable UUID that values store. Relabelling or disabling an option never touches values, and options are never deleted. Disabled options keep displaying on existing records but cannot be newly chosen.

**Values** (`custom_field_values`): one polymorphic table with one typed column per type (`value_text`, `value_number NUMERIC(18,4)`, `value_date`, `value_boolean`, `value_option_id`, `value_reference_id`). A CHECK requires exactly one column, matching `field_type`. Composite foreign keys make PostgreSQL verify that a row's type and entity type match its definition and that an option belongs to that definition, all within one organization. `entity_id` and `value_reference_id` are polymorphic, so the API validates them through the registry. "No value" is no row. Numbers use the strict decimal-string contract and dates are `YYYY-MM-DD`.

**References and dependencies.** A reference value stores the target UUID and display text is resolved live: a rename shows everywhere, a deactivated target shows with `active: false`, and a missing target renders `missing: true`. Only a new or changed value must exist in the active organization and be active (the same 422 for foreign and nonexistent ids). A dependent field is configuration: `source`, `depends_on` (a field key on the same entity type) and `filter` (a filter key the source registered). At definition time the engine checks that the source is referenceable, the filter exists, and the parent is an enabled reference field whose source equals what the filter references. At write time the child must satisfy `column = parent's value`; a child without its parent, or a parent changed or cleared with the child left behind, is refused. Existing pairs are history and are re-checked only when one of them changes. A chain (A, then B, then C) is simply several such definitions; `tests/test_generic_dependency_proof.py` proves Customer -> Project -> Work Order with a synthetic module and no engine change.

**Choices** (`GET /api/custom-fields/definitions/{id}/choices`): the engine queries the registered model through `scoped_select`, using only registry-declared columns and narrowing by `depends_on_value`. An id from another organization matches nothing.

**Required fields** are enforced when values are written (on the resulting set) and before a record is finalized: the capability registers a validator on the lifecycle seam that checks every enabled required field on the record and on all records below it (found through the registry's `parent` links). Making a field required later never invalidates existing records or unlocks completed ones; the next value write, or the next completion, must satisfy it.

**Locking.** The engine calls the entity's `is_editable` callback before validating or writing. Sales' callback row-locks the transaction (`FOR UPDATE`) and allows only drafts. It is the same lock every Sales lifecycle step takes, so a value write and a completion are serialized and each re-checks state after acquiring the lock, with neither side knowing the other's rules. `tests/test_custom_fields_concurrency.py` proves this with real, separate connections.

**Delete protection.** The custom-fields reference guard is registered on core. It counts only values of records that still exist, in the record's own organization, so values orphaned by a deleted line never block anything and one organization's data never protects another's records. Raw SQL deletes bypass guards, which is why dangling references render safely.

**Administration.** Creating or changing definitions and options is owner/admin only (via `roles_required`). Reading definitions, listing choices and writing values is open to every member.

**Not in V1:** filtering lists by custom fields, text search indexes, money/percent types, multiple dependencies per field, operators other than equality, formulas, deleting definitions, and orphan clean-up.

---

## Implementation notes: frontend

Next.js (App Router) in `frontend/`. Slice 1 (foundations) and slice 2 (Customers and Catalog) are implemented; Horses, Transactions and the custom-field renderer follow in later slices.

```text
Browser ──(same-origin /api/o/{orgId}/...)──► route handler (BFF) ──► FastAPI
Server components ──(lib/backend.ts)────────────────────────────────► FastAPI
                        lib/backend.ts is the ONLY place X-Dev-User-Email and X-Organization-Id are added
```

- **The organization is in the URL** (`/o/{orgId}/...`), never in a shared cookie, so two tabs can work in two organizations and a switch in one tab cannot re-target another tab's writes. The layout checks the id against the user's memberships (`GET /api/me/organizations`); a malformed, nonexistent and foreign id all end in the same 404. That check is a UI convenience: FastAPI independently verifies the user and the membership on every scoped request.
- **Switching organization is a full page load** (plain links). `OrgScope` additionally keys its subtree by `orgId`, and client fetches abort and ignore late answers, so state or data from one tenant cannot survive into another even if a switch ever happens client-side. Pages are dynamic and keyed by URL, so the client router cache cannot cross tenants. Cache Components stays off; enabling it would keep hidden routes alive (React `Activity`) and would need a per-organization key.
- **BFF (`app/api/o/[orgId]/[...path]/route.ts`)** forwards GET, POST, PATCH and DELETE only. It builds backend headers from scratch (client identity, organization, authorization and cookie headers are ignored), validates shapes only (UUID, a known API area, safe path segments, same origin via `Origin` against `Host`, JSON bodies up to 1 MB), turns backend redirects and failures into 502, and returns only the status and body.
- **Development identity** is an httpOnly cookie set by `/dev-login` for a user the backend knows, enabled only with `DEV_IDENTITY=enabled`. Everything asks `lib/identity.ts`, so real authentication later replaces that module and `lib/backend.ts`.
- **Server components** do initial reads; **client components** do interaction through `lib/api/client.ts`, which never throws for HTTP errors and maps 401/403/404/409/422 to a typed `ApiError` (`lib/api/errors.ts`): 422 locations become dotted field paths, and the structured 409 `validation_failed` keeps its `problems` for locating records and fields.
- **Decimals** (money, VAT, quantity, decimal custom fields) are strings in types, form state, payloads and rendering (`lib/decimal.ts`, `components/ui/DecimalText.tsx`). They are branded types validated by shape only and never converted to JavaScript numbers. ESLint forbids number conversion and rounding in the money-handling folders (scoped, not a global ban). Totals are calculated by the backend and displayed as received.
- **List and detail pages** (`app/o/[orgId]/customers`, `catalog`) read on the server through `lib/server-api.ts`, which answers like the BFF: no identity or a 401 goes to sign-in, a 404 (foreign, random or malformed id) shows the one generic not-found page, anything else reaches `error.tsx` without backend details. Search, status, type and page live in the URL (`lib/list-params.ts`, a plain GET form), are treated as untrusted input (unknown values are dropped, never forwarded) and page with one extra row requested instead of a count endpoint.
- **Forms** are client components. Each control is named after its API field, so a 422 location reaches its control with a plain lookup (`lib/forms.ts`); messages without a control are shown in a summary and are never dropped. Optional blanks are sent as `null`, edits send only the changed fields, `useMutation` allows one request at a time, and no `organization_id` is ever sent. Only the shape of a decimal is checked locally; digits, range and precision are the backend's rules.
- **Refresh strategy after a mutation:** `router.refresh()` (after `router.push()` for a create), because the Next.js client cache is reused on browser Back/Forward and would otherwise show a list visited before the change without it. There is no client data library.
- **Deliberately simple in V1:** hand-written API types, no form or data libraries, no design system, plain anchors and `confirm()`, no optimistic updates.
- **Tests:** Vitest for units and components; Playwright (against the installed Edge or Chrome) for a real stack on the dedicated test database (`postgres-test`): organization isolation across URL ids, switching, tabs, history and client state, forged headers, direct BFF requests, and database separation.
