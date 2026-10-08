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
- **Currency** is not on Item. It is an Organization setting (`default_currency`, never assumed) that each new transaction snapshots; see "Implementation notes: organization profile and currency".
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

**Currency.** A transaction carries a `currency` copied from the organization when it is created and immutable afterwards (trigger); old transactions keep NULL until an owner/admin assigns one explicitly. See "Implementation notes: organization profile and currency".

**Invoicing.** The approved design and its backend implementation are recorded in "Invoicing design (approved) and as built" below: whole transactions only, draft reservation, copied (never recomputed) amounts, snapshots at issuance. `transaction_lines` already has `UNIQUE (organization_id, id, transaction_id)` so invoice lines can reference a line of a given transaction.

---

## Implementation notes: organization profile and currency

**Business profile.** `organizations` and `customers` share seven optional columns (`BusinessProfile` in `app/models/mixins.py`): `address_line1`, `address_line2`, `postal_code`, `city`, `country_code`, `registration_number`, `vat_number`. Organizations also have `legal_name` (the name for documents; `name` stays the display name). Everything is nullable **free text**: blank input is stored as NULL, never as an empty string. The only structure enforced is the SHAPE of a country code (two capital letters; lower case is upper-cased first) by a Pydantic type and a CHECK. There is deliberately no jurisdiction logic: whether an address is complete or an identifier looks right is policy that belongs to whoever issues documents, and an invoice will not be blocked for missing profile data in V1.

**Organization settings API** (`app/api/organization.py`). `GET /api/organization` for any member of the active organization, `PATCH /api/organization` for owner and admin (`roles_required`). There is no id in the path or body: the organization is the one the membership resolves to, so a change can only ever reach the caller's own organization. Partial update; `extra="forbid"`; `name` may not be blank or null; `default_currency` may be set or changed but never cleared.

**Currency.** Prices are plain decimals without a currency of their own, so their currency is the organization's.

- `organizations.default_currency` is **nullable and never assumed**: no migration or default fills it. An owner or admin sets it explicitly (three capital letters; only the shape is checked). The seeded development and test organizations set SEK **explicitly** in their seed/factories; the platform itself assumes nothing.
- `transactions.currency` is a **snapshot** copied from the organization when a transaction is created. Creating a transaction in an organization without a currency is refused (`409 currency_not_configured`). It is never accepted from the client.
- A transaction's currency **never changes**. A PostgreSQL trigger (`trg_transactions_currency_immutable`) refuses any change away from a non-NULL value; NULL to a value is the one allowed transition.
- **Existing financial data is not relabelled.** Transactions that predate currencies keep `currency = NULL` ("no currency recorded"; shown as such in the UI). The migration does not backfill, and a test proves it on a database holding currency-less organizations and transactions. They get a currency only through `POST /api/transactions/assign-currency` (owner/admin): an explicit statement, confirmed in the UI, that they were priced in the organization's default; it names the currency, which must equal the default, touches only rows that have none, and moves their `version`. `GET /api/transactions/currency-status` reports how many are left. Until assigned they cannot be invoiced (a future invoice requires a currency).
- **Changing the default currency is refused once it would reinterpret stored prices** (`409 currency_locked`): when the current default is set, a different value is refused if the organization has any item or any transaction. The first setting (NULL to a value) is always allowed. **This is a temporary rule**: items have no currency of their own, so changing the organization's currency would silently turn 850 SEK into 850 EUR. It stays until a proper currency and repricing model exists.
- **Locking** (`app/core/currency.py`). Creating a transaction or an item takes the organization row `FOR SHARE`; a currency change takes it `FOR UPDATE` and checks the guards while holding it. A change therefore waits for in-flight creators and then sees their rows, and a creator arriving during a change waits and snapshots the new currency. Many creators do not block each other. Committed-data tests (`tests/test_currency_concurrency.py`) prove each ordering with separate connections.
- **Guards, not imports.** Core cannot know which modules hold prices, so modules register a currency guard on the registry (`registry.add_currency_guard`); Sales registers "transactions exist". Items belong to core and are checked directly.

---

## Implementation notes: organization time zone

`organizations.timezone` is an IANA name (e.g. `Europe/Stockholm`), nullable and never backfilled: nothing assumes a zone. `app/core/org_time.py` answers "what is today for this organization" (`today_in`, `organization_today`) from the clock seam (`app.core.clock.utcnow`), falling back to UTC when no zone is set (the behavior before zones existed). It is the ONLY source of server-side date defaults: a transaction or invoice created without a date gets it. Stored dates are plain calendar dates and never change when the zone changes; the zone only decides future defaults, so it may be changed or cleared at any time. Validation is the backend's (`zoneinfo` against the bundled `tzdata` package, an explicit dependency because slim images have no system zone database); the database CHECKs the shape only. `GET /api/organization` returns `timezone` and `today`, and the new-transaction page prefills its date with `today` from the server rather than the browser's date (the browser may be on another day). The Settings field offers the browser's zone names as suggestions only.

---

## Implementation notes: history (audit events)

**What is recorded.** `audit_events` (tenant-owned, append-only) holds one row per change: organization, time, actor (the user of the active membership), `entity_type` / `entity_id` (registry keys such as `customer`, `transaction_line`), an optional `context_type` / `context_id` (the record it belongs to: a line's transaction), an `action` (`created`, `updated`, `deleted`, lifecycle verbs such as `completed`, `issued`, and `fields_updated` for custom-field values) and `changes` (`{field: {from, to}}`, JSON-safe; decimals as the stored strings, ids as strings; custom-field changes carry the field's `label` and the display text a person saw). Technical columns (ids, organization, timestamps, authors, versions, positions) are never part of a change. Invoices record only their header fields; the frozen snapshots are documents, not history.

**How it is written.** Explicitly, by each write path, through `app/core/audit.py` (`created`, `apply_audited_update`, `updated`, `deletion`/`deleted`, `stamp`), in the SAME database transaction as the change. After-values are read back from the database after the flush, so `900` is recorded as the stored `900.00`. A write that changes nothing records nothing. A deletion that may be refused (`delete_or_409`) records its event only after the row is really gone (`after_delete=`). `created_by` / `updated_by` (the `Authored` mixin) are stamped at the same time; a change to a line or a custom-field value also stamps its transaction's `updated_by`.

**Append-only.** A trigger refuses UPDATE and DELETE. The single exception is a DELETE of an organization's own events while `app.deleting_organization` is set to that organization's id in the deleting transaction (for the organization deletion slice).

**Reading.** `GET /api/history?entity_type&entity_id` for any member: events of the record and of records whose context it is, newest first (at most 500), with the actor's name and the list of people named. A record of another organization simply has no history here. The frontend shows ids of customers and items as their names (looked up in the same organization; a deleted one says so) and timestamps in the organization's time zone.

**Not recorded (yet).** Organization settings, memberships and invitations (security events cover those today), and changes made before this existed (`created_by` NULL = "not recorded").

---

## Implementation notes: core registry and lifecycle seam

Generic capabilities and modules never import each other. They meet in **core**:

```text
customers, catalog, sales, domain modules ──► core registry ◄── custom fields (and future capabilities)
```

**Entity registry (`app/core/entity_registry.py`).** Each module calls `register(registry)` explicitly from `main.py` and says what it exposes: `EntityType(key, label, model)` plus optionally `custom_fields` (organizations may add fields to it), `reference` (other records may point at it: label/search/active columns and `FilterSpec`s, meaning "column X of this entity points at entity type Y"), `parent` (belongs to another entity type through a column) and `is_editable` (a callback answering "may this record's custom values change now?"). Registration fails fast on a misspelled column, a duplicate key or an unknown entity key (`registry.validate()` at startup). `registry.isolated()` lets a test register extras without leaving a trace.

**Lifecycle validation (`app/core/lifecycle.py`).** A module that owns a lifecycle step calls `ensure_valid(db, ctx, event, entity_key, entity_id)` before performing it. Sales does this for `complete`, `reopen` and `cancel` (events `COMPLETE`, `REOPEN`, `CANCEL`), after the state and `If-Match` checks and under the transaction row lock. A validator must ignore events it has no opinion on; today only required custom fields vote, on `complete`. Invoicing registers the validator that vetoes `reopen`/`cancel` of a transaction that is on an invoice; the seam itself is also tested with synthetic validators on `registry.isolated()`. Anything registered on the registry may veto by returning `Problem`s. A veto is a 409 with `{code: "validation_failed", event, message, total, problems: [{code, message, entity_type, entity_id, field, label}]}`, enough for a frontend to locate the record and field. Validators receive the `TenantContext` and only look at that organization.

**Currency guards.** `registry.add_currency_guard(guard)` lets a module say "my records make a currency change unsafe"; `registry.currency_lock_reason(db, organization_id)` asks them all. `isolated()` saves and restores them too.

**Authorization (`app/core/authz.py`).** `require_role(ctx, allowed_roles)` and the dependency factory `roles_required(*roles)`. The role is always the one in the ACTIVE membership, so a user who is an owner in one organization and a viewer in another gets the right answer in each; selecting an organization you do not belong to is still a 404.

**Viewers read, everyone else may write business records.** `RECORD_WRITERS` (owner, admin, accountant, employee) and its dependency `record_writer` guard every create, update, delete and lifecycle step on customers, items, horses, transactions and lines, and custom-field value writes. Narrower rules keep their own role sets (invoicing: owner/admin/accountant; settings and custom-field definitions: owner/admin). Membership administration and invitations decide authority inside the service from freshly locked rows, and leaving is open to every member. `roles_required` marks its dependency with `allowed_roles`, and `tests/test_viewer_read_only.py` walks every tenant-scoped write route: a write with no role dependency, or one that admits viewers, fails the suite unless it is on the short, reasoned exception list. The role is judged before any lookup, so a viewer gets the same 403 for its own record, a foreign one and a random id. The frontend mirrors the rule for presentation only (`lib/roles.ts` `canWriteRecords`, `lib/active-role.ts`): a viewer gets read-only detail views, no create links, a "not allowed" page instead of a create form, and a read-only transaction editor.

**Polymorphic delete guard.** `delete_or_409` also asks registered reference guards, so a record pointed at from somewhere a foreign key cannot express (a custom-field reference) cannot be deleted.

---

## Invoicing design (approved) and as built

The approved design, with the owner's modifications. The backend is implemented (see "Invoicing as built" below); the frontend and PDF are not. The prerequisites (profiles, currency, `UNIQUE (organization_id, id, transaction_id)` on `transaction_lines`, the `reopen`/`cancel` lifecycle events and the generic custom-field flag filter) are described in the other notes.

**Scope (V1).**
- **Whole transactions only.** One or more completed transactions may share an invoice only when they have the same billing customer **and** the same currency. No transaction or line splitting. Invoiced-ness is derived from `invoice_transactions` (a transaction is `none`, `on a draft invoice` or `invoiced`); nothing is added to Sales.
- **Draft, then issue.** Creating an invoice makes a draft that **reserves** its transactions and has **no number**. Issuing allocates the number and freezes the document. A draft can be deleted, which releases the transactions.
- **Reservation is why a draft's amounts stay put.** A reserved transaction cannot be reopened or cancelled (the lifecycle seam refuses it), so its lines stay immutable for as long as the draft exists; the draft's copied amounts cannot drift from their source.
- **No void or cancel of an issued invoice, and no credit notes, in V1.** Corrections are a later capability.
- **No `document_type` column.** Nothing needs it today; add it when credit notes are actually designed.

**Tables** (all tenant-owned; every cross-table reference is a composite `(organization_id, …)` key):
- `invoices`: `status` (`draft`/`issued`), integer `version` (`If-Match`, as in Sales), `customer_id`, `currency`, `customer_snapshot` and `issuer_snapshot` (versioned JSONB), `customer_name` (a plain copy for list and search), `invoice_date`, `due_date`, `description`, `series`, `number`, `number_text`, `issued_at`, `issued_by`, and the stored `net_amount`/`vat_amount`/`gross_amount`. Numbering uniqueness is `UNIQUE (organization_id, series, number)` **only**; `number_text` is a stored label and is **not** required to be unique (formatting policy is decided separately). A CHECK ties `status = 'issued'` to the number and issue columns being set.
- `invoice_vat_rows`: the stored VAT breakdown (never recomputed on read).
- `invoice_transactions`: the sources. `UNIQUE (organization_id, transaction_id)` makes double invoicing impossible. Composite foreign keys to `invoices (…, customer_id, currency)` and to `transactions (…, billing_customer_id, currency)` make the database enforce one customer and one currency per invoice (these need a unique key on `transactions`, added with that milestone; the child's `currency` column is NOT NULL so the foreign key is really enforced, which also means a currency-less transaction cannot be invoiced).
- `invoice_lines`: the Sales inputs and the three stored amounts **copied verbatim** (same three CHECKs as Sales lines), `UNIQUE (organization_id, source_line_id)`, and a composite foreign key to `transaction_lines (organization_id, id, transaction_id)`; custom-field snapshots as JSONB.
- `invoice_counters`: `(organization_id, series)` and `next_number`.

**Numbering.** Allocated at issuance from the counter table, in the same database transaction as the status change (`INSERT … ON CONFLICT DO UPDATE … RETURNING`, no `MAX()+1`, no PostgreSQL sequence). A failed issuance rolls the allocation back with everything else; an issued number is never reused; a deleted draft never had one. This is deliberately **not advertised as "gapless numbering"** and encodes no such business guarantee: whether a jurisdiction requires gapless series, and what a number looks like (prefixes, per-year series), is policy that can be layered on `series`/`number_text` later.

**Immutability.** Database triggers on the invoice tables reject UPDATE and DELETE of issued invoices and their children, and INSERT of children into a non-draft invoice. There is **no PostgreSQL trigger that makes the Sales lifecycle depend on Invoicing tables**. Blocking reopen and cancel is a cross-module rule and lives in the core lifecycle seam: Invoicing will register a validator for the `reopen` and `cancel` events (it is not registered yet because Invoicing does not exist). It is protected by locking and concurrency tests: the seam runs under Sales' transaction row lock, so a reopen and an invoice creation serialize on that row whichever comes first (`tests/test_currency_concurrency.py` already proves this ordering for a stand-in claim).

**Creating a draft.** One database transaction: authorize (owner/admin/accountant may mutate, every member may read), validate the id list (non-empty, no duplicates, at most 200), lock the transactions `ORDER BY id FOR UPDATE`, check status/customer/currency/not-already-invoiced under the locks, read lines, customer, issuer and `show_on_invoice` custom values, insert, copy amounts verbatim and check the totals equal the sources' stored totals, commit. `UNIQUE (organization_id, transaction_id)` is the last guard.

**Issuing.** Lock the invoice, then its source transactions in id order, then **re-verify the reserved transaction and line set and the financial snapshot** against the sources (status `completed`, the sources' versions and stored amounts, no line added or removed) before freezing anything; then re-take the party and custom-field snapshots, allocate the number, set `issued`, move `version`. Amounts are not recalculated: they are the immutable Sales snapshots.

**Snapshots.** Buyer and seller blocks (versioned, `schema: 1`), lines, and custom fields as one generic list per line/transaction (`key`, `label`, `field_type`, typed `value`, resolved `display`, `missing`, `position`) for enabled `show_on_invoice` definitions with a value. Read endpoints for an issued invoice touch only invoicing tables (a test records the SQL and fails otherwise).

**Dependencies.** Invoicing may import Sales and Custom Fields directly; the reverse is forbidden (`tests/test_module_boundaries.py` already forbids Sales, Custom Fields and Equine from importing `invoicing`, and anything outside its package and the wiring from importing it).

## Invoicing as built (backend; no frontend or PDF yet)

Implemented in `app/modules/invoicing/` (migration `f74d0b3c9e56`). The design above is what was built; this records the concrete choices.

**API** (`/api/invoices`, `/api/invoiceable-transactions`; reads for every member, mutations for owner/admin/accountant):

| Endpoint | Behavior |
|---|---|
| `GET /api/invoiceable-transactions?customer_id&date_from&date_to` | completed, currency set, on no invoice (draft or issued), with customer, totals and version |
| `GET /api/invoices/by-transaction?ids=` | per id `none`, `draft` or `invoiced` (+ invoice id and number text); an unknown or foreign id answers `none` exactly like a free transaction |
| `POST /api/invoices` | `{transaction_ids, invoice_date?, due_date?, description?}` creates a draft; the customer, currency and amounts are never accepted |
| `GET /api/invoices`, `GET /api/invoices/{id}` | list (status, customer, dates, `q` over customer name and number) and the whole stored document |
| `PATCH /api/invoices/{id}` | draft header only (dates, description), `If-Match` |
| `POST /api/invoices/{id}/issue` | `If-Match`; numbers and freezes |
| `DELETE /api/invoices/{id}` | draft only, `If-Match`; releases the transactions |

There is no cancel/void of an issued invoice, no credit note and no payment endpoint. Order of checks on every mutation of an existing invoice: found in the caller's organization (else the one 404) -> state (`409 invoice_issued`) -> `If-Match` (428 missing, 400 malformed, `409 stale_record` with `current_version`) -> change. Refusals of a creation are structured `409`s with a `code` and the (own) `transaction_ids` concerned: `transactions_not_completed`, `mixed_customers`, `currency_missing`, `mixed_currencies`, `already_invoiced`; ids that are not found in the caller's organization, any mixture of those with found ones, and random ids all give one identical `422` on `transaction_ids`.

**Tables and keys.** `invoices` (no `document_type`; numbering uniqueness is `UNIQUE (organization_id, series, number)` only, `number_text` is a stored label and is not unique), `invoice_transactions`, `invoice_lines`, `invoice_vat_rows`, `invoice_counters`. Header totals and VAT rows are `numeric(18,2)` (a sum of many lines can exceed one line's `numeric(14,2)`); lines keep the Sales columns and the same three CHECKs. Sales gained one key for the foreign key to point at, `UNIQUE (organization_id, id, billing_customer_id, currency)` on `transactions`, and nothing else: it stores no invoiced state and has no trigger that mentions invoicing. The currency columns of an invoice and its links are NOT NULL, so the composite foreign key to `transactions` is really enforced and a transaction without a currency can never be linked.

**Locking.** Creation locks the source transactions `ORDER BY id FOR UPDATE`, and checks, reads and copies under those locks. Issuing locks the invoice, then the sources in id order. Deleting and editing a draft lock the invoice. Reads that must reflect the locked rows bypass the session's identity map (`populate_existing`), so the verification does not depend on how the session was used. Creation inserts inside a savepoint, so a failure (including a lost race on `UNIQUE (organization_id, transaction_id)`, which is translated to the ordinary `already_invoiced` answer) leaves nothing behind.

**Issuing, step by step** (one database transaction): lock the invoice, check state and `If-Match`; lock the sources; verify that every source is still completed with the version, customer, currency and date the draft recorded, that the source lines are exactly the copied lines and that every copied value equals its source, and that the stored header and VAT rows are still the sums of the stored lines (any difference is `409 source_changed` and nothing is issued: the financial content is never recalculated or "repaired"); inside a savepoint, re-take the customer, issuer and custom-field snapshots, allocate the number from the counter (the last thing that can fail), set number, text, time, issuing user, status and version.

**Reservation.** `registration.py` registers a validator on the core lifecycle seam: for `reopen` and `cancel` of a transaction it looks for a link in `invoice_transactions` and answers a `invoice.reserved` problem for a draft or an issued invoice. Sales calls the seam under its row lock and imports nothing from Invoicing. Concurrency tests (`tests/test_invoices_concurrency.py`) prove both orderings with a request paused at a gate: a reopen underway makes a creation wait and then refuse, and a creation underway makes a reopen/cancel wait and then be refused.

**Immutability.** Two invoice-local trigger functions (`invoices_immutability`, `invoice_children_immutability`) refuse any change or delete of an issued invoice and its children, any insert of a child into an issued invoice, and, for a draft, any change other than the header data and the snapshot content (`fields`, the snapshots, the customer name). A draft's customer, currency, series and totals are fixed for life. The functions read only invoicing tables; a test asserts that no trigger on a Sales table involves invoicing. Deleting a draft cascades to its children (the trigger sees the parent already gone).

**Reading is independent of live data.** `read_invoice` and the list query only the invoicing tables; a test records every SQL statement of an issued-invoice read and fails if one names a live table, a control proves the recorder sees live reads elsewhere, and further tests change or delete every live source (customer, organization, item, fields, transactions, lines) and show the document is identical. The customer and issuer blocks are versioned (`schema: 1`) and hold only fields that exist today. Custom-field values flagged `show_on_invoice` are copied generically (`key`, `label`, `field_type`, typed `value`, resolved `display`, `missing`, `position`, `definition_id` for audit only); Custom Fields' value reads gained `position` and `definition_id` for this, which keeps the copy a single consistent read.

**Boundaries.** Invoicing imports core, the shared models/schemas, `sales.models`, `sales.pricing`, `sales.versioning`, `custom_fields.service` and `custom_fields.schemas`, and nothing else of Sales or Custom Fields; nothing imports Invoicing except the wiring files (`tests/test_module_boundaries.py`, both directions).

**Deferred or accepted debt.** Customer erasure/anonymisation against retention law (jurisdiction policy); `invoice_date` chosen by the client and defaulting to today (no organization time zone yet); PDF as its own later milestone (the stored snapshot is sufficient for it); payments; credit notes.

---

## Implementation notes: invoicing frontend

Minimal screens over the Invoicing API (`app/o/[orgId]/invoices/`, `features/invoices/`, `components/snapshots/`). Same architecture as Transactions: pages are server components that read through `serverRead` (identity from the cookie, organization from the URL, both added by `backendFetch`); the client part receives the server's record and replaces it by `router.refresh()` after every change; the browser reaches FastAPI only through the BFF.

- **A stored document, rendered from itself.** `InvoiceDocument` holds no state and fetches nothing. The invoice page and the list read only `/api/invoices...` (the list page reads one customer solely to label its customer *filter*). `features/invoices/boundary.test.ts` fails if invoice code imports customer, catalog, horse, transaction-editor or custom-field code, requests `/customers`, `/items`, `/horses` or `/custom-fields`, converts a decimal to a number or does arithmetic on an amount field. Custom-field snapshots go through `components/snapshots/FieldSnapshots`, a generic read-only renderer (stored label, stored display text, stored missing state; no definitions, choices or references), whose own boundary test enforces genericity (no metadata comparisons, no domain words) and inertness (no fetching, no effects).
- **Zero arithmetic.** Amounts, totals and VAT rows are the server's strings (`DecimalText`); `features/invoices/**`, `app/o/*/invoices/**` and `components/snapshots/**` are ESLint decimal zones. The create screen deliberately shows no combined total, because the backend offers no authoritative aggregate for a selection.
- **Selection rules in one pure module** (`eligibility.ts`): compatibility is a comparison of customer id and currency code with the first selected row; the create request is built there and carries ids and approved header fields only. FastAPI remains the authority and answers a structured conflict (`already_invoiced`, `transactions_not_completed`, `currency_missing`, `mixed_*`) when the list was stale.
- **Concurrency and unknown outcomes.** The invoice view runs one change at a time. A draft edit carries the version the editor was OPENED on; a stale refusal keeps the user's draft and offers an explicit discard. Failures are classified (`failures.ts`); `network` and `server` are `unconfirmed`: the outcome is unknown, so the invoice is re-read (`verify`) and no new Issue or Delete is offered until that check has answered. `invoice_issued`, `stale_record`, `source_changed` and 404 refresh to the authoritative state. Answers that arrive after the screen was left (navigation or organization switch) change nothing: every continuation is guarded by a mounted flag.
- **Permissions** are presentation only (`lib/roles.ts`); the backend's role checks are authoritative and tests forge BFF requests as employees and viewers.

---

## Implementation notes: custom fields (UDFs)

Implemented in `app/modules/custom_fields/`. It depends on core only and never names a concrete entity: everything it knows about entities comes from the registry. Attributes that are normal and stable for an entity stay real columns in that entity's module; custom fields extend an entity with organization-specific fields and never replace normal domain modeling.

**Definitions** (`custom_field_definitions`, tenant-owned): `entity_type` (registry key), `key` (slug, unique per organization and entity type), `label` (organization-defined), `field_type` (`text`, `number`, `date`, `boolean`, `select`, `reference`), `required`, `position`, `enabled`, `show_in_form`, `show_in_table`, `show_on_invoice`, and for references a `reference_source` plus an optional single dependency (`depends_on_definition_id`, `depends_on_filter`). Structural properties (`entity_type`, `key`, `field_type`, `reference_source`, the dependency) are immutable; label, required, position, enabled and the `show_*` flags can change. Definitions are disabled, never deleted. A field with enabled dependents cannot be disabled, and a dependent field cannot be enabled while its parent is disabled. Money and percent field types are deferred until currency exists.

**`show_on_invoice`** means the field is *eligible to be snapshotted* when an invoice is created. A future invoice must store the rendered label and value it needs at issuance; it must never resolve current definitions or reference targets to display an existing invoice.

**Flag filter.** `GET /definitions`, `GET /entities/{type}/{id}/values` and `GET /values` accept `?flag=required|show_in_form|show_in_table|show_on_invoice` (and `service.load_definitions` / `read_values` take `flag=`), returning only definitions with that yes/no property set, and the values of those. It is a generic filter on a property: Custom Fields has no invoice-specific code or dependency, and a test keeps the API's list of flags equal to the service's.

**Options** (`custom_field_options`): the choices of a select field, each with a stable UUID that values store. Relabelling or disabling an option never touches values, and options are never deleted. Disabled options keep displaying on existing records but cannot be newly chosen.

**Values** (`custom_field_values`): one polymorphic table with one typed column per type (`value_text`, `value_number NUMERIC(18,4)`, `value_date`, `value_boolean`, `value_option_id`, `value_reference_id`). A CHECK requires exactly one column, matching `field_type`. Composite foreign keys make PostgreSQL verify that a row's type and entity type match its definition and that an option belongs to that definition, all within one organization. `entity_id` and `value_reference_id` are polymorphic, so the API validates them through the registry. "No value" is no row. Numbers use the strict decimal-string contract and dates are `YYYY-MM-DD`.

**References and dependencies.** A reference value stores the target UUID and display text is resolved live: a rename shows everywhere, a deactivated target shows with `active: false`, and a missing target renders `missing: true`. Only a new or changed value must exist in the active organization and be active (the same 422 for foreign and nonexistent ids). A dependent field is configuration: `source`, `depends_on` (a field key on the same entity type) and `filter` (a filter key the source registered). At definition time the engine checks that the source is referenceable, the filter exists, and the parent is an enabled reference field whose source equals what the filter references. At write time the child must satisfy `column = parent's value`; a child without its parent, or a parent changed or cleared with the child left behind, is refused. Existing pairs are history and are re-checked only when one of them changes. A chain (A, then B, then C) is simply several such definitions; `tests/test_generic_dependency_proof.py` proves Customer -> Project -> Work Order with a synthetic module and no engine change.

**Choices** (`GET /api/custom-fields/definitions/{id}/choices`): the engine queries the registered model through `scoped_select`, using only registry-declared columns and narrowing by `depends_on_value`. An id from another organization matches nothing.

**Required fields** are enforced when values are written (on the resulting set) and before a record is finalized: the capability registers a validator on the lifecycle seam that checks every enabled required field on the record and on all records below it (found through the registry's `parent` links). Making a field required later never invalidates existing records or unlocks completed ones; the next value write, or the next completion, must satisfy it.

**Locking.** The engine calls the entity's `is_editable` callback before validating or writing. Sales' callback row-locks the transaction (`FOR UPDATE`) and allows only drafts. It is the same lock every Sales lifecycle step takes, so a value write and a completion are serialized and each re-checks state after acquiring the lock, with neither side knowing the other's rules. `tests/test_custom_fields_concurrency.py` proves this with real, separate connections.

**Delete protection.** The custom-fields reference guard is registered on core. It counts only values of records that still exist, in the record's own organization, so values orphaned by a deleted line never block anything and one organization's data never protects another's records. Raw SQL deletes bypass guards, which is why dangling references render safely.

**Administration.** Creating or changing definitions and options is owner/admin only (via `roles_required`). Reading definitions and listing choices is open to every member; writing values is open to every member except a viewer (`record_writer`, see "Authorization" in the core registry notes).

**Not in V1:** filtering lists by custom fields, text search indexes, money/percent types, multiple dependencies per field, operators other than equality, formulas, deleting definitions, and orphan clean-up.

---

## Implementation notes: optimistic concurrency (Sales)

Last-writer-wins is not acceptable for a record a person edits in a browser tab that may be minutes old. Sales therefore uses a small, explicit precondition (`app/modules/sales/versioning.py`), not a general versioning or event system:

- **Token:** an integer `version` per record. Not `updated_at`: a timestamp can tie, and a transaction's own timestamp does not move when one of its lines changes. `transactions.version` moves on ANY change (header, line, status); `transactions.header_version` only on header changes (so a header edit is not refused because someone changed a line); `transaction_lines.version` on edits of that line.
- **Transport:** the HTTP `If-Match: "<version>"` header, because `DELETE` and the lifecycle `POST`s have no body. Required on header edit, draft delete, line edit/delete and `complete`/`reopen`/`cancel` (428 if missing, 400 if malformed). Adding a line takes none (it commutes with other edits).
- **Answer:** `409 {"code": "stale_record", "entity_type", "entity_id", "current_version"}` (RFC 9110 would say 412; 409 keeps one family of "your view is out of date" answers next to the status conflicts). Nothing is changed.
- **Order of checks** (what keeps tenancy intact): the record is found in the caller's organization (404 for foreign and random ids whatever the header says) → the state allows the change (a completed transaction answers its own 409 first) → the version, under the same `SELECT … FOR UPDATE` row lock every Sales mutation takes → the change. A no-op write moves no version.
- **Not covered:** Customers, Items, Horses and custom-field values remain last-writer-wins; the same pattern applies when a screen needs it.

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
- **Development identity** is an httpOnly cookie set by `/dev-login` for a user the backend knows, enabled only with `AUTH_MODE=dev` and `APP_ENV=development` (S2 retired `DEV_IDENTITY`). It is now one adapter (`lib/identity.ts`) behind the credential abstraction in `lib/auth/credential.ts`; the real login is described under "Browser authentication (S2 as built)".
- **Server components** do initial reads; **client components** do interaction through `lib/api/client.ts`, which never throws for HTTP errors and maps 401/403/404/409/422 to a typed `ApiError` (`lib/api/errors.ts`): 422 locations become dotted field paths, and the structured 409 `validation_failed` keeps its `problems` for locating records and fields.
- **Decimals** (money, VAT, quantity, decimal custom fields) are strings in types, form state, payloads and rendering (`lib/decimal.ts`, `components/ui/DecimalText.tsx`). They are branded types validated by shape only and never converted to JavaScript numbers. ESLint forbids number conversion and rounding in the money-handling folders (scoped, not a global ban). Totals are calculated by the backend and displayed as received.
- **List and detail pages** (`app/o/[orgId]/customers`, `catalog`) read on the server through `lib/server-api.ts`, which answers like the BFF: no identity or a 401 goes to sign-in, a 404 (foreign, random or malformed id) shows the one generic not-found page, anything else reaches `error.tsx` without backend details. Search, status, type and page live in the URL (`lib/list-params.ts`, a plain GET form), are treated as untrusted input (unknown values are dropped, never forwarded) and page with one extra row requested instead of a count endpoint.
- **Forms** are client components. Each control is named after its API field, so a 422 location reaches its control with a plain lookup (`lib/forms.ts`); messages without a control are shown in a summary and are never dropped. Optional blanks are sent as `null`, edits send only the changed fields, `useMutation` allows one request at a time, and no `organization_id` is ever sent. Only the shape of a decimal is checked locally; digits, range and precision are the backend's rules.
- **Relationship pickers** (`components/ui/EntityPicker.tsx`): the picker is generic (a caller-supplied `search` returning `{id, label}` entities, a current `value`, `onChange`); the form that uses it may know what the entities are (`HorseForm` knows Owner and Stable are customers, `features/customers/customer-picker.ts` knows how to search them). The selection is an entity with an id, typed text never selects, only the id is submitted, and the current selection is displayed even if it is no longer offered. Each answer carries the request it was made for and is ignored if it is not the current one, if the list has been closed, or if the picker is gone; the organization scope removes the picker (and so all its results) when the organization changes. The backend decides what may be assigned: new assignments offer active customers only, and an unchanged reference is never re-sent, so a customer deactivated later does not block editing the horse. This is deliberately not the metadata-driven custom-field renderer (slice 5).
- **Transactions** (`features/transactions/`): the page is a server component and the single source of truth; `TransactionEditor` keeps no copy of lines or totals, only drafts, what is running, and what to tell the user. Components: `HeaderEditor`, `LinesTable` / `LineRow` / `LineEditor`, `AddLineForm`, `TotalsPanel`, `LifecycleBar`, `TransactionCreateForm`, with shared state in `editor-context` and the sorting of backend answers in `failures.ts`. Generic components (`EntityPicker`, `Field`, `ConfirmButton`, ...) know nothing about Sales.
  - *Snapshots:* a catalog line is created by sending only `{item_id, quantity}`; FastAPI copies the item, and the line is shown and edited as stored. No code path reads an item to render or fill a line. Editing a line never sends `item_id`.
  - *No arithmetic:* quantity, price, VAT, net, VAT amount, gross, totals and the VAT breakdown are decimal strings from FastAPI, printed as received (`features/transactions/**` is a lint-restricted decimal zone).
  - *Refresh:* after every successful change `router.refresh()` inside a transition re-reads the whole transaction; lines, totals and the header are dimmed and marked "updating…" until it arrives. One change at a time (a guard against two clicks in the same instant, plus disabled controls); lifecycle buttons wait while any editor is open, so completing never silently drops edits; a response that arrives after the editor is gone is ignored.
  - *Concurrency:* an editor judges its save by the version it was OPENED on (`line.version`, `header_version`; lifecycle steps and line deletes use the version on screen at the click). FastAPI refuses an old version with `409 stale_record` and changes nothing. The editor then keeps the draft, switches Save off, loads the latest around it (a refresh does not touch an editor's own state) and offers "Discard my edits and load the latest"; it also notices a newer version arriving through any refresh without sending anything. A transaction that is no longer a draft is explained and shown read-only. A tab that becomes visible refreshes itself only when no editor is open.
- **Custom fields in the frontend** (`components/custom-fields/`, `lib/custom-fields/`): a generic renderer driven only by the backend's field metadata. It understands six types (text, number, date, boolean, select, reference), the optional `depends_on` link between reference fields, `required`, `show_in_form` and `enabled`, and nothing about what a field is for. `components/custom-fields/boundary.test.ts` enforces genericity mechanically: the layer may import only React, the shared UI primitives, the shared `lib` and itself (never a `@/features/...` module), may not compare metadata to a literal (`key === "..."`, `source === "..."`, `entity_type === "..."`, or a `switch` on them), and may not mention domain words at all; the check is itself tested against bad samples. A module integrates the renderer with a thin wrapper (`features/transactions/TransactionFields.tsx`, `LineFields.tsx`) that supplies the record, the save function and the read-only state.
  - *Model* (`lib/custom-fields/model.ts`, pure): one `Draft` per field in its own type (a boolean is `true | false | null`; a number is a string; select and reference hold `{id, label, inactive}`), `buildChanges` (only what differs from the saved values, compared as stored, local checks for shape only), `applyChange` (setting a field clears every descendant, recursively, from the metadata's `depends_on` links), `shownValue` (read-only text; absence is "Not set", `false` is "No").
  - *Saving:* values are saved per record, together, in ONE request `PATCH /custom-fields/entities/{type}/{id}/values` with `{"values": {key: value|null}}`: the backend validates a record's values as a whole (dependencies against the final state, required fields on every write), so a parent change and its cleared children must travel together. Null clears. No Sales version is sent; the backend locks the record through its `is_editable` callback, which is also what serializes a write against a lifecycle step.
  - *Choices* come from `GET /custom-fields/definitions/{id}/choices` (generic), with `depends_on_value=<parent UUID>` for a dependent field; the BACKEND filters. A new search function is made whenever the parent's value changes and the picker drops every answer to the old one (`EntityPicker` ties answers to the search function that fetched them). Only active targets are offered; an existing inactive or missing target is displayed and never resent.
  - *Data flow:* the page reads definitions (`/definitions?entity_type=`) and values (bulk `/values?entity_type=&entity_ids=`) on the server, so initial state is authoritative and `router.refresh()` re-reads it after every change. A blocked completion (`409 validation_failed`) keeps its `problems`: the banner lists them with links, and the editor passes the ones about a record's fields to that record's panel as external errors, shown at the controls; they clear with the next change.
- **Refresh strategy after a mutation:** `router.refresh()` (after `router.push()` for a create), because the Next.js client cache is reused on browser Back/Forward and would otherwise show a list visited before the change without it. There is no client data library.
- **Deliberately simple in V1:** hand-written API types, no form or data libraries, no design system, plain anchors and `confirm()`, no optimistic updates.
- **Tests:** Vitest for units and components; Playwright (against the installed Edge or Chrome) for a real stack on the dedicated test database (`postgres-test`): organization isolation across URL ids, switching, tabs, history and client state, forged headers, direct BFF requests, and database separation.

---

## Invoice PDF (as built)

The PDF of an issued invoice, made by the backend, stored once, and served from storage forever. Implemented in `app/modules/invoicing/pdf/` (migration `a85e1c4d7f90`). The decisions below were approved before implementation.

**Lazy freeze (D1).** Nothing is rendered when an invoice is issued. The first successful `GET /api/invoices/{id}/pdf` renders the PDF from the stored invoice and stores it; every later request returns exactly the stored bytes. There is no regeneration in V1: a later change of the template, the fonts or the ReportLab version never touches an invoice that already has an artifact (`template_version`, `renderer` and `source_sha256` are stored so a future "superseding artifact" can be added without losing history).

**Storage (D2).** The artifact is a row in `invoice_pdfs`: tenant identity (`organization_id`), the invoice (composite FK `(organization_id, invoice_id)` to `invoices`), `content` (`bytea`), `byte_size`, `sha256`, `renderer` (library version + the hash of the pinned font list), `template_version`, `source_sha256` (SHA-256 of the canonical JSON of the document the PDF was rendered from) and the usual timestamps. One row per invoice (`UNIQUE (organization_id, invoice_id)`). CHECKs tie `byte_size`, `sha256` and the `%PDF-` magic to the content. An invoice-local trigger (`invoice_pdfs_guard`, which reads only `invoices`) refuses an INSERT for an invoice that is not issued and refuses every UPDATE and DELETE; the ORM refuses them too (`before_update` / `before_delete`). The bytes live next to the invoice they belong to, so backups, restores and tenant protection are the invoice's. No object storage.

**Flow (D4).** (1) read the invoice in the caller's organization (a foreign or random id is the one 404; a draft is `409 invoice_not_issued`); (2) if its artifact exists, return it; (3) build the immutable `PdfDocument` from `read_invoice` (invoicing tables only); (4) `commit()`: **no transaction and no row lock is open while ReportLab renders** (a test inspects `pg_stat_activity` and tries `FOR UPDATE NOWAIT` from inside the render); (5) `INSERT ... ON CONFLICT DO NOTHING`; (6) read the stored row for the invoice and return ITS bytes. Several first downloads at the same instant may each render; exactly one row becomes canonical and every caller receives that row's bytes, including the callers whose own rendering lost (tested with committed data, distinct renderings and gates). Any member who may read the invoice may trigger the first generation.

**Rendering boundary.** `document.py` (frozen `PdfDocument` of strings, text cleaning, canonical hash, input bounds), `format.py` (digit grouping and trailing-zero trimming by string handling), `fonts.py` (bundled fonts and the per-character choice), `render.py` (ReportLab), `build.py` (stored invoice read model to `PdfDocument`), `filename.py`. None of them has a database session or imports a database, domain, number or network module (`tests/test_pdf_boundary.py` enforces it on the syntax tree and tests the checker against samples that break each rule). Only `service.py` touches the database. The renderer cannot look anything up: after issuance the PDF depends on the stored invoice alone, which tests prove by changing and deleting every live record before the first download.

**Zero calculation.** The renderer prints stored strings: quantity, unit price, VAT rate, line net/VAT/gross, the VAT breakdown and the invoice net/VAT/gross. Presentation formatting is string handling ("1062.50" becomes "1 062.50", "1.000" becomes "1"); no number type exists in the rendering layer, so nothing can be rounded, added or reconstructed. Tests feed deliberately inconsistent figures and assert each is printed unchanged and that no recomputed value appears. Amount columns are sized from the widest printed figure (layout measuring only), and figures are laid out without ReportLab's CJK wrapping, which would otherwise break a grouped amount at its no-break spaces.

**Content (D6 to D13).** English labels, locale-neutral numbers (dot decimal separator, no-break space grouping), A4, issuer and customer from the stored snapshots, invoice number, dates, currency, description, lines (position, description, unit, quantity, unit price, VAT %, net, VAT, gross), the VAT breakdown and the totals once after the last line. No draft PDF. Custom-field snapshots are printed generically as `label: text`: transaction-level fields under "Reference information" (grouped by the stored source date, with no heading when there is one source), line-level fields under their line. A missing reference prints the stored "(no longer existed)". Nothing about payment (bank details, Swish, QR) is invented: none is stored.

**Many pages, long text (D9).** `LongTable(repeatRows=1, splitInRow=1)` with a numbered footer (`Invoice N · Page x of y`). Long text wraps and splits across pages; nothing is truncated. There is NO page limit. Before any layout the input is bounded: at most 2,000 lines, 20,000 custom-field entries and 1,000,000 characters of text in total (`422 document_too_large` otherwise), and the rendering is capped at 64 MiB as a guard against a bug, not as a rule about invoices.

**Fonts and capability debt (D3, D5).** Only bundled fonts are used (`pdf/fonts/`; never the operating system's): Noto Sans (four faces), Symbols, Symbols 2, Math, Sans SC and Sans KR, pinned by SHA-256 (`SHA256SUMS`, verified at first load and by tests), under the SIL OFL with the license notices kept in `pdf/fonts/licenses/` and the provenance in `pdf/fonts/SOURCES.md`. The font for each character is chosen deterministically (primary face, the regular face, then the fallbacks in a fixed order); no missing-glyph box is ever drawn. What it covers is a statement about the renderer, not a business rule: Latin (including extended and Vietnamese), Greek, Cyrillic, common symbols and mathematics, Han, kana and Hangul. Characters the renderer cannot draw correctly refuse the PDF with `422 unsupported_characters`, naming them, and **nothing is stored**: scripts that need shaping or right-to-left layout (Arabic, Hebrew, the Indic scripts, Thai and others), combining marks that have no precomposed form, and anything no bundled font contains (for example emoji). Lifting that needs a shaping engine (HarfBuzz), not a change of invoice data; it is listed as technical debt in `TODO.md`. Italic faces lack three rare characters; those fall back to the upright regular face.

**Determinism.** ReportLab runs in `invariant` mode, so the same document renders to the same bytes in any process (tested with a second process). The stored artifact is nevertheless the source of truth, not a re-render.

**Hostile text.** Every stored string is cleaned (NFC, control, invisible and bidirectional-override characters removed) and then XML-escaped before ReportLab's Paragraph parser sees it; only markup this module generates reaches the parser. The PDF contains no links, JavaScript, forms, attachments or external references (tests scan the bytes), nothing is fetched from text (`trustedHosts` and `trustedSchemes` are emptied, and tests make the network raise), and the download filename is sanitized independently of the content (`invoice-<number>.pdf`, ASCII letters, digits, dot, underscore and hyphen, at most 60 characters of number).

**API contract.** `GET /api/invoices/{id}/pdf`: `200 application/pdf` with `Content-Disposition: attachment; filename="invoice-<number_text>.pdf"`, `Content-Length`, `ETag` (the artifact's SHA-256, quoted), `X-Content-Type-Options: nosniff`, `Cache-Control: private, no-store`; `404` (foreign, random or deleted: one answer), `409 invoice_not_issued`, `422 unsupported_characters` (with the first characters and the total) or `422 document_too_large`.

**BFF and frontend (D11).** The BFF relays everything as JSON text except this one path, which is a binary pass-through: only a plain `GET /api/o/{org}/invoices/{uuid}/pdf` (no query string, no other method), requested with `Accept: application/pdf`. The upstream answer is validated before a byte reaches the browser: status 200, content type exactly `application/pdf`, a `Content-Disposition` that starts with `attachment`, a declared length equal to the bytes read and within the limit, the `%PDF-` magic, and an `ETag` equal to the SHA-256 of the body. Anything else is a 502. The response headers are rebuilt from validated values (the filename is accepted only in the backend's own shape, otherwise `invoice.pdf`); no upstream header is copied. A PDF content type anywhere else is a 502 too. The browser fetches the file as a Blob and saves it with an anchor `download`; nothing is generated or calculated in the browser. The "Download PDF" button exists only for an issued invoice, for every role (reading is enough), shows "Preparing PDF…" while the first request makes the file, and explains 404, 409, 422 (listing the characters) and server errors; pressing it again is always safe.

**Deferred.** Regeneration or a superseding artifact; localization and locale-aware formats; payment details and QR codes; email; credit notes; complex-script support (a shaping engine); an organization logo; a draft preview. Each is its own milestone.

**Verification of the PDF milestone.** Beyond the tests, 100 deliberate faults were injected one at a time (tenant filtering, draft generation, artifact mutation, live lookups, financial recomputation, markup escaping, filename handling, BFF binary handling, first-download races, fonts, layout) and each had to make a test fail. Every fault was detected except those that are redundant by design, which are named here so nobody mistakes them for gaps: (a) the organization filter on the stored-artifact read is redundant because the invoice is looked up in the caller's organization first (the observable difference, a foreign DRAFT answering 409 instead of 404, is tested through the invoice lookup); (b) the composite foreign key `(organization_id, invoice_id)` is redundant with the insert trigger, which reads the invoice by both columns; (c) taking a row lock on the invoice before the commit that precedes rendering changes nothing, because the commit releases it (removing the commit is detected, by the open-transaction and `FOR UPDATE NOWAIT` checks).
---

## Production authentication and membership administration (approved design; S1 built)

**Status.** The design below is approved. **S1 (backend identity core)**, **S2 (browser authentication)**, **S3 (organization onboarding)**, **S4 (membership administration)** and **S5 (invitations)** are implemented; hardening and documentation (S6) remain. The development workflow (`AUTH_MODE=dev`, `/dev-login`) is unchanged.

**Two questions, kept apart.** *Authentication* answers "who is this user?": a `User` (stable internal id) proven by a password and a server-side session. *Membership* answers "what may this user do in this organization?": `organization_users` (role per organization), resolved by `get_tenant_context` exactly as before. Nothing in the authentication code reads a membership, a role or an organization (a static test enforces it), and the tenant code reads no credential, session or token table.

### Approved decisions

| | Decision |
|---|---|
| D1 | Application-owned email and password, **Argon2id** (`argon2-cffi`, no custom cryptography). Credentials live in `user_credentials`; `users` stay the stable identity; a future `user_identities(provider, subject, user_id)` is the seam for OIDC or SSO. |
| D2 | **FastAPI-owned** opaque server-side sessions. The BFF turns the protected browser session cookie into an `Authorization: Bearer` header. |
| D3 | 12 hours idle, 7 days absolute, 20 sessions per user (all settings). |
| D4 | `users.can_create_organizations` gates organization creation. |
| D5, D6 | An admin manages accountant, employee and viewer only; owners manage admins and owners. Member and invitation administration is visible to owner and admin only. |
| D7 | No ownership-transfer workflow in V1: promote another owner, then demote or leave. |
| D9 | No general email verification in V1; an invitation stays bound to the normalized invited email. |
| D10 | Invitation and setup links carry their secret in the URL **fragment**, which the page removes from the history at once; a token is never shown again. |
| D11 | The operator bootstrap CLI is the only first-user and recovery path. |
| D12 | The onboarding UI requires a currency; the organization API and model stay nullable. |
| D13 | Production requires `AUTH_MODE=session` and fails closed at startup; the dev identity additionally requires `APP_ENV=development`; there is no fallback. |
| D14 | Passwords are 12 to 128 characters with no composition rules. |

### Review change 1: the CSRF and BFF trust boundary (the responsibility split)

```
Browser ──────────────► BFF ──────────────────► FastAPI
cookies, Origin,        validates Origin vs     authenticates the opaque session;
CSRF header             PUBLIC_ORIGIN; validates validates sha256(X-CSRF-Token) against
                        the double-submit        THAT session's csrf_hash (constant time);
                        cookie/header pair;      never treats a browser Origin or cookie as an
                        STRIPS every client      authentication or authorization assertion
                        Authorization, identity,
                        tenant and proxy header
```

- **Browser to BFF.** The browser sends its cookies, an `Origin` header and a CSRF header. The BFF validates the browser `Origin` against the configured `PUBLIC_ORIGIN` (not the `Host` header) and validates the double-submit relationship (the CSRF cookie equals the CSRF header). It discards every client-supplied `Authorization`, `Cookie`, identity (`X-Dev-User-Email` and the like), tenant (`X-Organization-Id`) and proxy or trust header (`X-Forwarded-*`, `X-Client-Ip`).
- **BFF to FastAPI.** `Authorization` is built **only** from the protected session cookie. `X-Organization-Id` is built only from the URL route where applicable. `X-CSRF-Token` is forwarded only after the BFF validated it. `If-Match` stays validated as today. For the throttling source the BFF alone sets `X-Client-Ip`, and FastAPI honours it only with `TRUST_CLIENT_IP_HEADER=true` (valid because FastAPI is reachable from the BFF alone).
- **FastAPI.** It authenticates the opaque session from the Bearer header and, for every mutating request (anything but GET, HEAD, OPTIONS), validates `sha256(X-CSRF-Token)` against that session's `csrf_hash` with `hmac.compare_digest`. Binding the token to the session makes a cookie planted from a sibling domain useless. FastAPI never sees, and never expects, the browser's cookies or Origin: that check is the BFF's, and nothing is left ambiguous between the two.
- **Before there is a session (login, setup-link redemption, invitation acceptance).** The same split with a pre-auth token: the login and invitation pages set a random `__Host-` pre-auth cookie and the form or `fetch` must echo it. The BFF validates `PUBLIC_ORIGIN` and that double-submit pair **before forwarding**; FastAPI receives no cookie and no Origin. The throttling in the next section is FastAPI's own defense in depth and does not depend on the browser.
- **Implemented in S1** (FastAPI half only): the Bearer session, the per-session `csrf_hash` and its constant-time check on every mutating request, and `X-Client-Ip` handling. The BFF half (Origin, double-submit, header stripping, cookies) is S2.

### Review change 2: login abuse protection (revised algorithm, implemented in S1)

No account-level state exists: `user_credentials` has no failure counter and no `throttled_until`, so an attacker who only knows an email can neither lock the account nor extend a lock on it. The inputs are the **source** (a client address, an IPv6 /64), the **login identifier** (an HMAC of the normalized email, so unknown and known accounts are treated alike and the table never holds an email), recent `login_failure` **events**, and the global **Argon2 capacity**. Parameters are settings; the defaults are in brackets.

For every `POST /api/auth/login`, in this order:

1. **Per source.** Failures from this source in the window (15 minutes, a sliding window) at or above `throttle_source_max_failures` (30): refuse with 429.
2. **Per source and identifier.** At or above `throttle_pair_max_failures` (5): 429.
3. **Per identifier across all sources** (distributed guessing). At or above `throttle_identifier_max_failures` (50): only a source that has **already logged in successfully as this identifier** (within the retention) may still try; any other source gets 429. A user on their usual source is therefore never affected by an attacker's failures.
4. **Global valve.** Failure rows written in the window at or above `throttle_global_max_failure_events` (5000): failures stop being recorded and, as in 3, only known sources may still try.
5. **Bounded admission to hashing.** At most `argon2_max_concurrent` (4) hashes run; at most `argon2_max_waiting` (8) more requests wait, for at most `argon2_wait_seconds` (2); any further request is refused **at once** with 503 and `Retry-After`, with no hashing, no queue growth and no event.
6. **Exactly one Argon2 verification** inside the slot, against a dummy hash made with the current parameters when there is no user or no credential. Unknown account, wrong password, no credential and disabled account return the identical 401 and cost the same.
7. **On failure** a `login_failure` event is recorded (unless the valve is open). On success: lock the user row, re-check it is active, store a rehash if the parameters are outdated, end the presented session, create a fresh session, enforce the cap, record `login_success`, purge a small batch of old records, commit.

A refused attempt (429 or 503) **writes nothing**. A block therefore cannot be kept alive by hammering it: it lasts only until the failures that caused it leave the window.

- **Growth is bounded.** A failure row is written only after steps 1 to 4 passed, so per window one source can cause at most its limit (plus the requests already in flight, at most the admission limit) and the whole table at most the valve (plus the same slack). A row is fixed and small (type from a fixed list, source up to 64 characters, a 64-character HMAC, a 64-character code). Every count is an index range scan that stops at its limit (`LIMIT cap`).
- **Known trade-offs.** A user logging in from a source they have never used, while at least 50 failures per window hit their identifier from many sources, waits until those failures age out; known sources are never blocked. If the valve opens (5000 failure rows in a window, which takes more than 160 sources at 30 each) unknown-source logins are refused everywhere until the window passes. A botnet is limited to at most 5 guesses per source per window and 50 in total against unknown sources per window. Successful logins write a row each but cost a full Argon2 hash under admission.
- Setup-link redemption has a per-source failure budget (`setup_source_max_failures`, 10); a wrong current password on `change-password` has a per-user budget (`password_change_max_failures`, 5).
- No Redis or distributed rate limiting: one PostgreSQL instance is the counter store.

### Review change 3: the owner invariant (new organizations: built in S3; existing organizations: built in S4)

- **New organization.** `POST /organizations` inserts the organization **and** the caller's owner membership in **one** database transaction; an injected failure between the two inserts rolls everything back. The owner-loss trigger does not duplicate or guarantee this.
- **Existing organization with at least one owner.** The application locks the actor, the target and all owner rows (`ORDER BY id FOR UPDATE`, fresh reads) and re-checks authority and the remaining-owner count inside the transaction; a deferred database trigger backs it up. A transition from at least one owner to zero is impossible.
- **Legacy organization already at zero owners** (the seed's second organization is one). The migration is compatible and does not invalidate it; the trigger fires only when an owner row is lost, so unrelated changes to such an organization still work. Operator tooling (S4) can repair it.
- The trigger stays focused on **owner loss**.

### Review change 4: `security_events` retention

A minimal append-only table, **not the business Audit module**. It exists because throttling needs durable counters and an investigation needs facts. IP addresses are security metadata only. Retention is a setting, not an invariant: `SECURITY_EVENT_RETENTION_DAYS` (default **30**), and `AUTH_RECORD_RETENTION_DAYS` (30) for ended sessions and used or expired setup tokens. The database only insists on a floor: a row younger than one day cannot be deleted (so the setting must be at least 1), and a row can never be updated. **Never stored** in `security_events` (tests prove it): passwords, session tokens, CSRF tokens, setup tokens, invitation tokens, emails (the identifier is an HMAC), or any free text beyond a 64-character code.

### S1 as built

**Schema** (migration `b96f2d4e8a13`, additive; downgrade drops exactly it):

| Table | Columns and rules |
|---|---|
| `users` | `+ can_create_organizations boolean NOT NULL DEFAULT false` |
| `user_credentials` | `user_id` PK/FK, `password_hash` (CHECK: Argon2id encoding), `password_changed_at`, timestamps. No counters. |
| `auth_sessions` | `id`, `user_id`, `token_hash` UNIQUE, `csrf_hash` (both CHECK 64 hex), `created_at`, `last_used_at`, `absolute_expires_at`, `revoked_at` + `revoked_reason` (CHECK: set together, from a fixed list), `user_agent` (200), `source` (64). Partial index of active sessions per user; index on expiry. |
| `user_setup_tokens` | `id`, `user_id`, `token_hash` UNIQUE (CHECK 64 hex), `purpose` (`set_password`), `created_at`, `expires_at`, `used_at`, `revoked_at`. A partial unique index allows one outstanding link per user and purpose. |
| `security_events` | `id` identity, `occurred_at`, `event_type` (CHECK: fixed list), `actor_user_id` (nullable FK), `source`, `identifier_hash` (CHECK 64 hex), `detail`. Indexes for the three throttle counts and for purging. Trigger: no UPDATE, no DELETE of a row younger than a day. |

**Session resolution** (`get_current_user`, one mechanism per `AUTH_MODE`, no fallback): read exactly `Authorization: Bearer <43-character token>` (anything else is unauthenticated); look up `sha256(token)` (unique index); require the session unrevoked, `absolute_expires_at > now`, `last_used_at > now - idle`, and the user active, **on every request** (disabling a user and ending a session take effect on the next request; roles and memberships are never cached in a session); for a mutating request require `X-CSRF-Token` matching `csrf_hash` (403 `csrf_failed`); refresh `last_used_at` at most every `session_touch_seconds` (300; the idle limit can therefore be up to 5 minutes stricter than the setting). The development header and `DEV_USER_EMAIL` are not read in session mode.

**Sessions.** A token is 256 random bits (`secrets.token_urlsafe(32)`), returned once and stored only as its SHA-256. A login always creates a fresh session and ends the one presented with the request (fixation); a password change and a link redemption end the user's other sessions; the cap revokes the oldest sessions under a per-user row lock, so simultaneous logins cannot exceed it. Ended and expired records are purged by `purge` and, in small batches, at every login.

**Endpoints** (`/api/auth`, only in session mode; 404 otherwise; called by the BFF, never by a browser): `POST /login`, `POST /logout`, `POST /change-password`, `POST /setup`. `/api/me` and `/api/me/organizations` work unchanged over sessions.

**Bootstrap and recovery** (`python -m app.scripts.admin`): `bootstrap-user` creates a user **without any credential** and prints a single-use link (`<PUBLIC_ORIGIN>/setup#<token>`, 24 hours); `reissue-setup-link` revokes earlier links and prints a new one (recovery); `disable-user` (also ends every session), `enable-user`, `purge`. There is no way to supply a password (no option, no prompt, no environment variable). The token is 256 random bits, shown once, stored only as its hash, redeemed by one conditional UPDATE (single use, race-safe), refused after expiry or revocation, and recorded nowhere. Redeeming sets the password, ends the user's other sessions and signs in. A weak password or a request that cannot use the link does not consume it, and a malformed or unknown link costs no Argon2 work. Running the CLI needs shell and database access, the same trust as the database; no endpoint creates a user without an invitation or a link.

**Migration behavior for existing users.** The migration adds tables and one column and changes no row: **no credential, session or setup-token row is created**, and `can_create_organizations` is false for everyone. A seeded development user therefore cannot authenticate in session mode until an operator issues a link, and `seed_dev` creates no credential (tested).

**Configuration** (`AUTH_MODE` is `dev`, `session` or `disabled`; unknown values refuse to start): production (`APP_ENV=production`, the default) requires `session` and a `SECURITY_KEY` of at least 32 characters (the HMAC key for identifiers) or the process does not start; `dev` requires `APP_ENV=development`; development may choose any mode (session mode without a key gets a per-process key). Deployment constraints: FastAPI reachable from the BFF alone, `CORS_ORIGINS=[]`, `TRUST_CLIENT_IP_HEADER=true` behind the BFF, Argon2 parameters tuned on the deployment hardware, `PUBLIC_ORIGIN` set.

**Deviations from the proposal.** (1) The throttling replaces the proposed per-account delay (review change 2). (2) `user_credentials` has no `failed_count` or `throttled_until`. (3) Setup-link redemption (`POST /api/auth/setup`) is implemented in S1, because the single-use and expiry rules cannot be proved without it; the setup page is S2. (4) CSRF enforcement is applied once, in the authentication seam, to every mutating request in session mode (so later endpoints are covered by construction) instead of per endpoint. (5) The `X-Client-Ip` header is trusted only when `TRUST_CLIENT_IP_HEADER` is on (default off), so S1 is safe before the BFF exists.
---

## Browser authentication (S2 as built)

S2 connects the real browser to the S1 backend identity core. Still NOT built (later slices): organization onboarding (S3), membership administration (S4), invitations (S5). The development workflow (`AUTH_MODE=dev`, `/dev-login`) is unchanged and independent.

```
browser ──cookies, Origin, X-CSRF-Token──► Next.js BFF ──Authorization: Bearer <token>──► FastAPI
                                           reads the protected cookie server-side;       resolves the session to a User,
                                           validates Origin and the CSRF pair;            validates sha256(X-CSRF-Token)
                                           builds every upstream header FROM SCRATCH      against that session, resolves
                                                                                          X-Organization-Id to a membership
```

### The mode boundary (one place)

`lib/auth/config.ts` is the only code that reads `AUTH_MODE`, `APP_ENV`, `PUBLIC_ORIGIN` and `TRUSTED_PROXY_HOPS` (a static test enforces it), and only the shell, the authentication pages and the BFF routes branch on the mode (also tested). Business pages and components call `requireCredential()` and `serverRead()`; they never learn what kind of identity it is.

| `AUTH_MODE` | Meaning | Requirements |
|---|---|---|
| `dev` | the development identity (`/dev-login`, dev-user cookie) | `APP_ENV=development`, otherwise the mode is "none" |
| `session` | the real login | `PUBLIC_ORIGIN` (https outside development) |
| anything else | no identity at all ("none") | the BFF answers 503 to everything |

`APP_ENV` defaults to production, like the backend. The retired `DEV_IDENTITY` switch does nothing. `instrumentation.ts` stops a production server that starts with an unusable configuration. In `session` mode `/dev-login` and `/api/dev-session` do not exist (404), the dev header and cookie are never read, and a failed session never falls back to anything.

### Cookies

| Cookie | Production name (https) | Development name (http) | Attributes | Holds |
|---|---|---|---|---|
| session | `__Host-bp_session` | `bp_session` | **HttpOnly**, SameSite=Lax, Path=/, no Domain, Secure over https, expires with the session's absolute end | the opaque FastAPI session token |
| CSRF | `__Host-bp_csrf` | `bp_csrf` | readable by script (the double-submit needs it), otherwise as above | the CSRF token returned for the session |
| pre-auth | `__Host-bp_pre` | `bp_pre` | HttpOnly, Lax, Path=/, 30 minutes | a random value, used only before a session exists |

The names and `Secure` follow `PUBLIC_ORIGIN`, never the request. The session token is never in a response body, a readable header, a URL, the page, any storage or the React state (tests, including a Playwright spec that scans the page and every response header). Clearing a cookie repeats the attributes it was set with: a `__Host-` cookie can only be overwritten or deleted by a response that is itself Secure, Path=/ and without Domain.

### The BFF trust boundary

For an authenticated request the BFF reads the session cookie server-side and builds `Authorization: Bearer <token>`; `X-Organization-Id` comes only from the `/api/o/{orgId}/…` route; `If-Match` is validated as before; for a mutation it forwards `X-CSRF-Token` after validating it. **Every other header is built from scratch**: a client-supplied `Authorization`, `Cookie`, `X-Dev-User-Email`, `X-Organization-Id`, role or permission header, and any `X-Forwarded-*`, `Forwarded`, `X-Real-IP` or `X-Client-Ip` header are ignored and never reach FastAPI. The BFF asserts no user id, email or role. FastAPI stays authoritative: Bearer token to session to User; CSRF token to the session's `csrf_hash`; organization selector to membership to role. A backend 401 (expired, revoked, disabled) becomes a fixed answer naming the login page; a 403 (role, CSRF) and a 404 (tenant isolation) are relayed unchanged and are never treated as a lost session.

### CSRF: the responsibility split, as built

| Layer | Checks | Where it is proved on its own |
|---|---|---|
| BFF, state-changing requests (session mode) | the browser `Origin` equals `PUBLIC_ORIGIN` (a missing one is refused; `Host` is not consulted), and `X-CSRF-Token` equals the readable CSRF cookie | Vitest (`session.test.ts`, `request.test.ts`) and Playwright (`forgery.spec.ts`: "BFF layer") |
| FastAPI | `sha256(X-CSRF-Token)` equals the authenticated session's `csrf_hash`, constant-time; the browser's Origin and cookies are never examined | backend suite (S1) and Playwright ("FastAPI layer": cookie and header set to the same wrong value, which the BFF accepts and FastAPI refuses) |
| Before there is a session (login, setup) | the same Origin rule, and the **pre-auth double-submit**: `GET /api/auth/pre` sets an HttpOnly cookie and returns the same random value, the page echoes it in `X-Pre-Auth`, the BFF compares them before contacting FastAPI. The value is not a credential and is never forwarded. | Vitest (`auth.test.ts`) and Playwright (`login.spec.ts`) |

Deleting either layer is detected (fault injection). Invitation acceptance (S5) will use the pre-auth mechanism; it is not implemented.

### Login, setup and logout

- **Login** (`/login`): email and password only. Every wrong credential (unknown email, wrong password, no credential, disabled user) shows one message, `Invalid email or password.`, and sets nothing. The BFF validates Origin and the pre-auth pair, asks FastAPI, sets the two protected cookies and returns only a validated relative destination. A presented session cookie is passed upstream (built from the cookie) so FastAPI ends it: a planted cookie is never adopted.
- **Where login leads**: `/` and `/o/<uuid>` with plain path segments and a short query string; everything else (absolute URLs, `//host`, backslashes, schemes, control characters, encoded slashes or dots, dot segments, the authentication pages, the API) becomes `/`. The text is judged as written and after URL normalization, and the result is rebuilt from the parsed path, never copied from the input. The BFF validates again; the page validates the BFF's answer.
- **Setup** (`/setup#<token>`, session mode): the link secret is read from the URL fragment once (again on a `hashchange`, because opening another link in the same tab is only a fragment change), removed at once with `history.replaceState`, kept only in a ref, sent only in the body of the POST, never in a URL, a header, storage or the DOM, and never shown. Password plus a confirmation (a UX check); the backend's policy wording is shown for a weak password and the link stays usable; an invalid, used, expired or revoked link gets one generic message. Success follows the S1 contract: the link is consumed, the password set, and a session starts (the BFF sets the cookies like a login).
- **Headers**: `/login`, `/setup` and `/api/auth/*` are served `Cache-Control: no-store` and `Referrer-Policy: no-referrer` (`next.config.ts`); the setup page loads nothing from another site.
- **Logout**: a CSRF-protected POST. FastAPI revokes the session; **this browser's cookies are always cleared**, whatever FastAPI answers. The answer is honest: `confirmed`, `already_invalid`, or `unconfirmed` (FastAPI unreachable or odd), and the login page then says the browser is signed out but the server session could not be confirmed ended. A revoked token cannot be used directly against FastAPI (tested).
- **Session loss**: a server-rendered page whose backend call answers 401 redirects to `/login?next=<the organization page>`; a client request that answers 401 does a full page load of the login with the current page as the way back (only under `/o/`, never for the login pages themselves, so there is no loop). Expired, idle, revoked and disabled-user sessions are all tested end to end.
- **Navigation**: the organization stays in the URL; authentication is global to the browser session; there is no active-organization cookie. The shell shows the user from `GET /api/me/user` (new, returns the authenticated user without any organization context), not from anything the browser holds.

### Client address and throttling (the topology decision)

The BFF is the only caller of the authentication endpoints, and FastAPI is meant to be reachable from it alone. `TRUST_CLIENT_IP_HEADER` stays **off by default** and S2 does not turn it on. What exists now: with `TRUSTED_PROXY_HOPS=N` (default 0) the BFF takes the address the N-th trusted reverse proxy appended to `X-Forwarded-For` (entry N from the right: whatever the browser wrote further left is never used) and sends it as `X-Client-Ip` to the login and setup endpoints only; a browser-supplied `X-Client-Ip` or `X-Forwarded-For` cannot become that value. With 0 hops nothing is forwarded. FastAPI honours the header only with `TRUST_CLIENT_IP_HEADER=true`, valid only while FastAPI is reachable from the BFF alone. **Consequence until the deployment milestone configures both**: FastAPI sees the BFF as the only source, so the per-source login budgets act as one budget shared by all users (the known-source exemption and the per-account protections still apply, and nothing about identity or sessions is weakened). Source-based throttling becomes fully effective when deployment sets `TRUSTED_PROXY_HOPS` and `TRUST_CLIENT_IP_HEADER` together.

### Verification, trade-offs and deviations

- A second Playwright configuration (`playwright.session.config.ts`, `npm run test:e2e:session`) runs real authentication end to end on its own ports (BFF 3101, FastAPI 8002) against the disposable test database: the session-only specs (`e2e/session/`) and the representative dev specs (tenant isolation, customers, catalog, horses, transactions, invoices) unchanged, signing in through `signIn`. Credentials are provisioned through the real mechanism (the operator CLI's setup link); `seed_dev` creates none. The backend of that run is started with `DEV_USER_EMAIL` set on purpose, to prove it is ignored. Specs about forged dev headers and direct dev-header reads of FastAPI stay in the dev run.
- The CSRF cookie is readable by script by design (double-submit). The session cookie persists until the session's absolute end (idle and absolute limits are enforced server-side regardless). `GET /api/auth/pre` is a GET that sets a cookie: another site can only make a victim's in-flight login fail (a nuisance), never read or use the value. No Content-Security-Policy is set yet (a candidate for hardening).
- NextResponse shows an internal `x-middleware-set-cookie` header in unit tests; the real server sends only `Set-Cookie` (a Playwright spec asserts no `x-middleware-*` header).
---

## Organization onboarding (S3 as built)

S3 lets an authenticated user create an organization and become its owner. Still NOT built (later slices): membership administration (S4), invitations (S5). Authentication identifies the User; `organization_users` decides tenant membership and role. Nothing below creates a shortcut around that.

```
authenticated User ──► POST /api/organizations ──► ONE transaction:
 (session or dev seam)    (not tenant-scoped)        lock the user row (fresh read), judge users.can_create_organizations
                                                     INSERT organizations
                                                     INSERT organization_users (role = owner, user = the authenticated user)
                                                     INSERT organization_creation_requests (only with an Idempotency-Key)
                                                     INSERT security_events (organization_created)
                                                     COMMIT
        ──► full navigation to /o/{id}  ──►  the ordinary tenant path: membership read, X-Organization-Id selector, role
```

### Who may create an organization

- Only a user whose **account** has `users.can_create_organizations = true`. It is a property of the account, set by an operator, **not derived from any membership role** (owning an organization does not allow creating another) and not set by creating one. There are no plans, quotas or billing.
- **Operator CLI only, no application UI:** `python -m app.scripts.admin grant-org-creation --email ...` / `revoke-org-creation --email ...`. They change that one column (never a membership or a role) and record a `capability_changed` security event (`org_creation_granted:cli`, `org_creation_revoked:cli`, with `:unchanged` appended when nothing changed; no email, no secret). `bootstrap-user` already sets it by default (`--no-org-creation` to refuse). `seed_dev` gives it to `fredrik@dev.test` only, in the disposable seed data.
- The frontend shows the creation controls only when `GET /api/me/user` says the flag is true. That is **presentation**: FastAPI judges the flag on every request (a forged request from an account without it is a 403, tested end to end).
- It is not part of `/dev-login`. In dev mode the creator is resolved through the same dev identity seam as everything else and the same flag applies (no bypass).

### The contract

`POST /api/organizations` (BFF: `POST /api/organizations`, a dedicated route, not part of the `/api/o/{orgId}/...` catch-all, so the organization-scoped door can never be used to reach it)

| | |
|---|---|
| Request body | `name` (required, trimmed, 1 to 255), `default_currency` (**required**, three capital letters, upper-cased), optional `legal_name` and the profile fields (`address_line1`, `address_line2`, `postal_code`, `city`, `country_code`, `registration_number`, `vat_number`). Unknown fields are refused (422): there is **no** owner, user, role, creator or organization id field. |
| Header | `Idempotency-Key` (optional): 43 URL-safe base64 characters (32 random bytes), generated by the browser. A malformed key is a 422. |
| Success | **201** with the new `OrganizationRead` (the same shape as `GET /api/organization`); **200** with the same shape when the key was used before with the same body (see Retries). |
| Errors | 401 not authenticated (or CSRF: 403 `csrf_failed`), 403 `organization_creation_not_allowed`, 409 `request_key_conflict`, 422 validation. Nothing says whether any other organization exists. |

The owner is always the authenticated user. Client-supplied `X-Organization-Id`, role, owner or dev-identity headers are irrelevant (the BFF never forwards them; FastAPI ignores any that arrive).

### One transaction, locking

- **Atomic unit.** The organization, the owner membership, the retry record and the event are inserted inside one savepoint and then committed once. If anything fails nothing remains; an organization without its owner can never be committed, and there is no compensating deletion and no background task. `app.core.organizations._between_inserts` is a no-op seam between the two main inserts; tests make it fail (and pause) to prove, from another connection, that nothing is visible or left behind.
- **Fresh permission and serialization.** The service locks the creator's `users` row (`SELECT ... FOR UPDATE` with `populate_existing`, so the flag is read from the database and never from the identity map `get_current_user` filled earlier) and judges the flag under that lock. The same lock serializes two creations by one user and a capability change by the operator (`auth_service.set_creation_capability` takes it too): a revoke waits for a creation in flight, which completes under the right it held, and a revoke that commits first refuses the creation. Lock order is always `users` first, then the rows it inserts; nothing locks a user while holding an organization row, so the order cannot invert (a mixed creations-and-toggles race test completes with no deadlock and no organization without exactly one owner).
- The last-owner trigger is **not** part of S3 (it comes with S4).

### Retries: the idempotency decision

A browser can lose the response after the server committed. The browser therefore generates a high-entropy request key per attempt and sends it as `Idempotency-Key`. `organization_creation_requests` (`PRIMARY KEY (user_id, request_key)`, `organization_id` unique, key and hash shape CHECKs) is written in the same transaction as the organization.

- Same creator, same key, same body: **200** with the organization created the first time, **only while the creator is still a member of it** (through the current membership; its role may have changed). If they are not, the answer is a 409, never the organization.
- Same key, different body (compared by SHA-256 of the validated body): **409**; nothing is created.
- The key is scoped to the authenticated creator: another user presenting the same key creates their own organization and can never replay someone else's.
- The authority check (flag) comes first: a replay after the right was revoked is a 403.
- A failed attempt writes no record, so the same key can be retried after a server error without colliding.
- Without a key nothing is deduplicated (each request creates). There is **no** uniqueness on organization names, per user or globally: two tenants may share a display name.
- Deliberately small: one table, one lookup under the user lock, no platform-wide idempotency framework.

### The currency rule at onboarding

The currency is required and chosen explicitly: the form starts empty, nothing is preselected, nothing is inferred from a country, locale or browser, and the API refuses a missing or invalid code. It reuses the existing representation unchanged: `Organization.default_currency`, the shape-only `CurrencyCode` (three capital letters), the same typed code as the Settings screen, and the existing lock once items or transactions exist. **There is no supported-currency catalogue in the platform** (Settings is a free-text three-letter field), so onboarding does not invent one: it is the same typed code, not a dropdown. No exchange rates, no multi-currency.

### Profile fields

Onboarding asks only for what is needed to create and use the tenant: name and currency. The business profile (legal name, address, registration and VAT numbers) is completed later in Settings; the API accepts it already but the form does not ask. Invoicing validation is unchanged (issuing still requires its own prerequisites).

### The URL stays the tenant selector

After a 201 or 200 the page does a **full navigation** to `/o/{id}`. There is no active-organization cookie, storage, session or `User` column; two tabs can work in two organizations (tested), and the new organization is an ordinary membership: the shell, `GET /api/me/organizations` and the home list read it with no special casing (role `owner`). Eligible users reach creation from the home page (explicit link; with no organization, the notice offers it) and from the organization switcher in the shell. A user with exactly one organization is still redirected from `/` into it; the switcher link is their path.

### Ambiguous outcomes in the browser

The form keeps the key and the exact details of the last attempt. If the outcome is unknown (network failure, or a 5xx after the request may have committed) it says so, and "try again" resends the **same key and body**, so the backend returns the organization it already created. A definite refusal (422, 403) discards the key; changed details are a different request and get a new key (the notice says to check "your organizations" in case the first attempt did succeed). A double click is one request.

### Security event

`organization_created`: stable actor user id, the new organization id (`security_events.organization_id`, added in the migration, nullable, **no foreign key** so an event outlives anything, indexed partially), event type, timestamp, source and a non-secret `detail` (`keyed` or `unkeyed`). The form's contents (name, currency, profile) are never recorded; failed validation and refusals are not events (no Audit module). `security_events.record` stays free of tenant concepts: the organization service writes this row itself, and the authentication-boundary test allows exactly one marked exception (the `organization_id` reference column in `models/auth.py`).

### Legacy ownerless organizations

Existing organizations with no owner (the seed's second organization) are not repaired, altered or assigned: creation touches only the rows it inserts (tested at both levels). Every organization created through S3 begins with exactly one owner, the creator.

### Schema (migration `c07a3e5f9b24`, additive, reversible)

`organization_creation_requests`; `security_events.organization_id` plus its partial index; the `ck_security_events_type` CHECK widened with `organization_created` and `capability_changed`. Downgrade removes the two new kinds of event first (the append-only trigger is lifted for that one statement and restored) and then restores the CHECK; upgrade changes no existing row and grants no right to anyone.

### Verification, trade-offs and deviations

- Tests: backend (`test_organization_creation.py`, `test_organization_creation_concurrency.py` on committed data with genuine waiting, `test_migration_org_creation.py`), Vitest (BFF route in both modes, the form's retry and no-storage behaviour, boundary tests) and Playwright (`e2e/onboarding.spec.ts` in both the dev and the session run, `e2e/session/onboarding.spec.ts`: real login, the two CSRF layers, the operator's grant and revoke taking effect immediately).
- The idempotency key is optional at the API (a script can call it without one); the browser always sends one.
- A replay by a user who left the organization is a 409, not a 404: the key is theirs, but the organization is no longer visible to them.
- Currency selection is a typed code (see above), not a list: stopping there would have left no way to onboard, and there is no second currency system.
---

## Membership administration (S4 as built)

S4 administers EXISTING memberships: list, change role, remove, leave. It adds no invitations, user creation, email, custom RBAC, ownership-transfer workflow or organization deletion (invitations are S5). Everything is decided by the backend; the frontend only presents.

### The authority matrix

| Actor | May do | May NOT do |
|---|---|---|
| **owner** | change another member to ANY role; remove another member; promote to owner; demote another owner; demote **themselves**, or **leave**, only while another owner remains | remove themselves through the administrative removal (leave is explicit) |
| **admin** | change/remove members whose CURRENT role is accountant, employee or viewer, to accountant, employee or viewer only | touch an owner or another admin; grant admin or owner; remove an owner or admin; change their own role |
| **accountant / employee / viewer** | leave | list members; change or remove anyone |

Self rules: nobody changes their own role except an owner stepping DOWN while another owner remains (a "change" to the role one already has is a no-op, no event); every member may leave; the last owner may not. The matrix is restated independently in the test (`expected_change`) for every actor/target/new-role combination.

### Contracts (all tenant-scoped: `X-Organization-Id`, resolved against the caller's memberships; a non-member gets the usual 404)

| | |
|---|---|
| `GET /api/members` | owner/admin only (403 otherwise). `[{id, name, email, role, is_you}]` ordered by name. `id` is the **membership** id. No user id, credential or session state, no `can_create_organizations`, nothing about other organizations. |
| `PATCH /api/members/{membership_id}` | body `{"role": "<role>"}` and nothing else (unknown fields are a 422: no organization id, actor, actor role or target user/email). 200 with the member. |
| `DELETE /api/members/{membership_id}` | 204. |
| `POST /api/members/leave` | 204. Any member. |
| Errors | 403 `membership_admin_forbidden` (not an owner/admin), `insufficient_authority` (the target or the resulting role is beyond an admin), `self_role_change_not_allowed`, `self_removal_use_leave`; 404 `Member not found` (a random id and another organization's id are identical) and `Organization not found` (the actor lost their membership meanwhile); **409 `last_owner`** (the one stable conflict, whether the attempt was a demotion, a removal or a leave). |

### Fresh authority under lock (and the lock order)

The mutation endpoints use the tenant context only to learn the organization and who is acting; they never use its role. Every mutation decides from the database inside its own transaction:

**LOCK ORDER.** One statement: `SELECT ... FROM organization_users WHERE organization_id = :org ORDER BY id FOR UPDATE`, with `populate_existing`. It locks the actor row, the target row and every owner row (they are all rows of that organization) in **ascending membership id**, so all membership mutations of one organization take the same locks in the same order: they serialize and cannot deadlock with each other. Rows of other organizations are never read, counted or locked, so administration in one organization never waits for another (tested). Locking the whole set is deliberate: an organization has a handful of members, and it makes "who else is an owner" exact.

After the lock: re-check that the actor still has a membership (else 404), the actor's CURRENT role, the target's membership (else 404) and CURRENT role, the requested resulting role, and only then the owner count. Nothing is taken from the tenant context captured earlier, from the frontend, or from an ORM object loaded earlier (`populate_existing`; tested with deliberately stale loaded rows, and with gated requests that are paused between authentication and the lock while the other side commits).

### The last-owner invariant

Application: any operation that can reduce the owner count (owner to non-owner, removing an owner, an owner leaving) counts owners from the locked rows and refuses with `last_owner` if the result would be zero. Removal of an owner by someone else cannot reach zero through the API (the actor, an owner, remains), so that check is a defence in depth; demotion and leave are the reachable cases.

Database backstop (migration `d18b4c6e2f31`): two **deferred constraint triggers** on `organization_users` (`AFTER DELETE`, and `AFTER UPDATE OF role, organization_id`, each `WHEN` the OLD row was an owner and, for an update, it stopped being one in this organization), running at COMMIT. The function takes a per-organization advisory lock (`pg_advisory_xact_lock`) and counts that organization's owners; none left (and the organization still exists) raises `check_violation` (`organization_owner_required`). Properties:

- deferred, so one transaction may demote an owner and promote another;
- per-organization count (tested against an organization that has many owners);
- the advisory lock makes two raw transactions that each remove a DIFFERENT owner of a two-owner organization unable to both commit (write skew; tested with real concurrent transactions: one commits, one is refused);
- it fires only for a row that WAS an owner: a legacy organization that already has no owner stays valid and its unrelated changes (and any change of non-owner rows) are unaffected; it does not guarantee the initial owner of a new organization (S3's creation transaction does) and it repairs nothing; inserts are not checked;
- a refusal at commit is mapped to the same `last_owner` conflict by the service.

### Legacy ownerless organizations

The migration changes no row. Ordinary administration of such an organization works (the backstop only looks at lost owner rows). Repair is an operator action, never HTTP: there was NO existing operator command for roles, so S4 adds the smallest one, `python -m app.scripts.repair owner --organization-id <uuid> --email <member>`: it requires both identifiers, requires the user to ALREADY be a member (it never creates a membership), refuses an organization that already has an owner, changes that one role to owner and records an `owner_repaired` security event. It lives in its own tenant-aware module because `app.scripts.admin` is authentication-only (an existing static test forbids tenant concepts there). There is no application ownership-repair UI.

### Removal and leaving

Removal deletes the membership row only: the User, their sessions (authentication is global), their other memberships and historical references such as `invoices.issued_by` are untouched. Their next request to that organization fails through ordinary tenant resolution (404). Leaving is a separate endpoint with its own rules (no administrator authority needed; the last owner is refused); the generic removal refuses to be used on oneself (`self_removal_use_leave`).

### Security events

`member_role_changed` (`detail`: `<target user id> <old>><new>`), `member_removed` (`<target user id> <old role>`), `member_left` (`<old role>`), `owner_repaired` (`cli <old>>owner`), each with the actor user id and `organization_id`. Ids and roles only: no email, name or request body; a refused or no-op attempt records nothing. No schema column was added beyond the widened event-type CHECK; rich before/after auditing is left to the future Audit module.

### Frontend

`/o/{orgId}/members` (owner/admin; the navigation entry is shown only to them and the page shows a notice to other roles; presentation only): a table with name, email and role, a role control and a Remove (with confirmation) where `lib/members.ts#offered` says the apparent role allows it. **Leave organization** is a separate control in the shell header for every role. Every change is a request the backend decides; after ANY answer the list is re-read from the server, a refusal is shown as such (never as success) with a safe message (`last_owner`, not allowed, no longer exists), and a stale row settles into the truth. The last-owner rule is not calculated in the browser beyond hiding a self-demotion that cannot be legal. After leaving, the browser does a full navigation to `/`: the person stays signed in, keeps their other organizations, and sees the existing no-organization state (with the creation link only if the account may create organizations).

### Verification, trade-offs and deviations

- Tests: `test_membership_admin.py` (the matrix, self rules, last owner, freshness with stale loaded rows, tenancy with look-alike organizations, removal side effects, events, the trigger with raw SQL, the operator repair; run under the dev identity and real sessions with CSRF), `test_membership_admin_concurrency.py` (committed data, real connections: mutual demotion/removal/leave of two owners, remove versus demote, promotion versus leave, leave versus removal, role change versus removal, gated stale-authority races, per-organization lock isolation, a mixed burst without deadlock, the raw-SQL write skew, the legacy organization at a real commit), `test_migration_member_admin.py`, Vitest (matrix presentation, stale-UI messages, leave) and Playwright (`e2e/members.spec.ts` in the dev and session runs, `e2e/session/members.spec.ts`: CSRF layers and global sessions after removal).
- A role change to the role a member already has is a no-op (200, nothing written or recorded).
- A refused `remove` of the last owner is unreachable through the API by construction (see above), so the application check there is redundant defence, reported as such by fault injection.
- Deadlock handling is by construction (one ordered lock statement), not by retry; a deadlock would surface as an error, not be hidden. The raw-SQL backstop under concurrency takes an advisory lock instead of row locks for the same reason.
- `GET /api/members` returns `is_you` instead of a user id so the UI can mark the current person without exposing identifiers.
---

## Invitations (S5 as built)

S5 completes: authenticated organization administrator, create invitation, copy the bearer link, the invitee opens it, an existing or new account authenticates, and the invitation creates an ordinary membership. There is no email delivery, deployment, payments or audit UI. Authentication still identifies the User and `organization_users` still decides tenant membership and role: **the invitation token is not authentication, not tenant selection, and not authority to choose a role or to modify an existing membership.** The organization and the role come only from the locked invitation row.

### Schema and lifecycle (`organization_invitations`, migration `e29c5d7a3b48`)

`id`, `organization_id` (tenant-owned, FK), `email` (normalized with the authentication module's `normalize_email`; NOT a foreign key; CHECK that it is stored trimmed and lowercase), `role` (CHECK on the five roles), `token_hash` (SHA-256 hex, **unique index**, shape CHECK), `expires_at`, `created_by`, `created_at`/`updated_at`, `revoked_at`, `accepted_at`, `accepted_by` (CHECKs: never both revoked and accepted; accepted time and user come together). The row is immutable except those lifecycle pairs, so there is no "invitation role changes while someone accepts" race. Nothing is ever deleted.

States: **pending** (neither revoked nor accepted, not expired) -> **accepted** | **revoked**; **expired** is pending whose `expires_at` has passed (not usable; `INVITATION_TTL_DAYS`, default 7). Superseded and regenerated invitations are simply revoked.

**Pending uniqueness.** A partial unique index on `(organization_id, email) WHERE revoked_at IS NULL AND accepted_at IS NULL` allows at most one invitation per organization and email that is neither revoked nor accepted. It deliberately does NOT mention expiry (`now()` cannot be in an index predicate): an expired row keeps its slot until it is **explicitly superseded**: creating (or regenerating) an invitation for that email revokes the expired one first, in the same transaction, with an `invitation_revoked` event whose detail ends in `superseded`. A still-unexpired pending invitation makes a second create a 409 `invitation_pending` (use regenerate). Inviting someone who is already a member is a 409 `already_member`.

### The token and the copy-link threat model

256 random bits (`secrets.token_urlsafe(32)`, 43 URL-safe characters), returned **once**, in the response that creates (or regenerates) the invitation. Only SHA-256(token) is stored; the raw token is in no log, no security event, no list, no database column. There is no "show it again": regenerate = revoke the old row and create a new random token in one transaction.

The link is `/invite#<token>`. A URL **fragment** is never sent to any server: not in the request line, not in `Referer`, not in a redirect built by a server, not visible to server components. The invite page therefore (1) is a server component that renders without the secret (it only asks who is signed in), (2) reads the fragment in the browser, (3) removes it at once with `history.replaceState`, (4) keeps it only in a `ref`, never in rendered state, storage, cookies or query strings, (5) sends it only in the BODY of POSTs, (6) is served `Cache-Control: no-store` and `Referrer-Policy: no-referrer` and loads nothing from another site. A reload cannot recover it (the person opens the link again); the administrator's panel that shows a freshly created link likewise lives in component memory only. Honest limits: the link is a bearer secret (anyone who has it can try it; for an existing account they must also hold the invited account), it is visible in the browser the administrator copies it from, and clipboard and chat history are outside the platform's control. A leaked unused link is cured by revoking or regenerating the invitation.

### Contracts

Administration is tenant-scoped (`X-Organization-Id`, a non-member gets the usual 404): `GET /api/invitations` (owner/admin; `{id, email, role, created_at, expires_at, state}`, never a token or hash), `POST /api/invitations` (`{email, role}` only; 201 with the same fields plus `token`), `POST /api/invitations/{id}/regenerate` (201 with a new token), `DELETE /api/invitations/{id}` (204; idempotent; 409 `invitation_accepted` for an accepted one). Errors: 403 `insufficient_authority` / `membership_admin_forbidden`, 404 `Invitation not found` (a foreign id and a random id are identical), 409 `already_member`, `invitation_pending`, `invitation_accepted`, `invitation_not_pending`.

The invitee's endpoints are NOT organization-scoped: `POST /api/invite/preview` (pre-auth; `{token}` in the body; returns `{organization_name, email, role, account_exists}` only), `POST /api/invite/accept` (an authenticated account; ordinary session CSRF), `POST /api/invite/accept-new` (pre-auth, session mode only; `{token, name, password}`; returns a session plus the organization id). Every unusable token (random, malformed, revoked, expired, already used by someone else) is the same `404 {"detail": "Invitation not found"}`. A wrong signed-in account is a specific `403 invitation_wrong_account` and consumes nothing; an existing account's creation attempt is `409 account_exists`; a weak password is `422 password_policy`. The preview tells the holder whether the invited email already has an account; that is the approved trade-off (the invitation is bound to that email), and nothing is revealed about any other address.

### Authority (decided from fresh locked rows)

Owner: may invite any role and revoke any invitation. Admin: may invite, regenerate and revoke only accountant/employee/viewer invitations. Accountant/employee/viewer: nothing. Create, regenerate and revoke never use the tenant context's role: they take the S4 lock, re-read the actor's membership and current role, and decide (tested with a stale loaded role and with requests paused between authentication and the lock).

### Existing account

The invitee signs in on the invite page through the protected login (the invitation's email is fixed; the token stays in the page's memory and goes through no redirect, `next`, cookie or storage), then accepts. A signed-in account is accepted only if its normalized email equals the invitation's: the bearer token alone is not enough. The wrong account is refused without consuming the invitation, and the page offers "sign out and continue" without losing the in-memory token. **Already a member:** acceptance never alters an existing membership: the invitation is settled, the existing role is kept (an existing viewer with a stale admin invitation stays a viewer) and the answer says `joined: false`.

### New account

`accept-new` creates User (email from the invitation, the name given), credential (the existing Argon2 service and password policy; the password is checked against the policy and hashed BEFORE any write, using the existing bounded hashing admission, and only for a token that is currently usable, so garbage costs nothing), membership (role from the row), session and the settled invitation, in ONE transaction. A failure at any point (tested with a failure injected after the user, credential and membership were flushed) leaves no user, credential, membership or accepted invitation. A weak password consumes nothing. If the email gained an account meanwhile (another invitation, same email, different organization, accepted a moment earlier) the loser gets `account_exists` with its invitation untouched, signs in, and accepts as an existing account.

### Single use and retries

Exactly one acceptance creates the membership. The same account retrying a successful acceptance gets an idempotent success (`joined: false`) while it is still a member; anyone else, or the same person after leaving, gets the generic 404; a retried account creation gets the generic 404 (no second user, no password reset, no second session; the person signs in with the password they chose).

### Combined lock order (with membership administration)

1. the organization's membership rows (`ORDER BY id FOR UPDATE`, S4's single statement);
2. the invitation row (`FOR UPDATE`);
3. the accepting user's row (acceptance only).

Create, regenerate and revoke take 1 then 2. Acceptance starts from the token: it reads the invitation without a lock only to learn the organization, then takes 1, 2, 3 in the same order and re-checks everything. Membership administration (S4) takes only 1. Every operation that takes several takes them in this order, so none can deadlock with another; inverting it is a detected fault, and a mixed burst of invitation and membership operations on committed data completes without a 500. Acceptance only INSERTS a membership; "am I already a member?" is answered by a fresh statement after the locks (a membership committed by another path meanwhile is detected by the unique constraint inside a savepoint and tolerated: the invitation is settled and the role kept). Acceptance cannot reduce the owner count, so it needs no last-owner check, and S4's database backstop is untouched. An owner invitation accepted into a legacy ownerless organization is not a special case: nobody can issue it through ordinary authority there, so the operator repair (`app.scripts.repair`) remains the recovery.

### Pre-authentication CSRF

The invite page's unauthenticated requests (preview, account creation, and the existing account's login) reuse S2's pre-auth double submit (`GET /api/auth/pre`, an HttpOnly cookie echoed in a header, checked by the BFF before FastAPI is contacted, plus the Origin check). The signed-in acceptance uses the ordinary session CSRF at both layers (BFF double submit, FastAPI's session-bound hash).

### Security events

`invitation_created` (`<invitation id> <role>`), `invitation_revoked` (`<id> <role>` | `<id> superseded` | `<id> regenerated`), `invitation_accepted` (`<id> <role>` or `<id> existing`), each with actor and organization; never a token, hash, password, email or body.

### Frontend

The Members page (owner/admin) gains Invitations: a form (owners may pick any role, admins accountant/employee/viewer; presentation only), the freshly created link shown once with a copy button and the "cannot be retrieved again" notice, a list of pending/expired invitations (never a token) with regenerate and confirmed revoke where the apparent role allows. `/invite` (session mode only) handles the four cases (signed in as the invited account, signed in as another account, signed out with an account, signed out without one) and ends with a full navigation to `/o/{organization_id}`; no active organization is stored anywhere.

### Trade-offs and deviations

- The tenant list reads without taking locks; only mutations lock.
- Regeneration is one atomic endpoint instead of revoke-then-create from the browser.
- Preview and account creation are limited to session mode (the dev identity has no sign-in page); the signed-in acceptance endpoint works in both modes at the backend.
- An invitation's expiry is checked at use; there is no background job that revokes expired rows (they are superseded on the next create).

---

## Product changes from staging testing: approved decisions (NOT implemented)

Decided by the owner on 2026-10-08. The slices are listed in `TODO.md`.

### Lifecycle effects (core extension)

The lifecycle seam (`app/core/lifecycle.py`) today lets a registered validator VETO a step. It gains **effects**: a module registers an action for an event (`COMPLETE`, `REOPEN`, `CANCEL`), and Sales runs the effects after the transition, under the same transaction row lock and inside the same database transaction. An effect that fails rolls the whole step back. Sales still imports nothing from the modules that react; Inventory is the first user. Validators still run first, so a vetoed step never reaches an effect.

### Inventory and backorders

- **Scope.** Items get `sku` and `track_stock` (products only; a service never holds stock). Inventory is its own module and talks to Sales only through the registry and lifecycle effects.
- **Physical stock is a ledger.** An append-only, tenant-owned `stock movements` table: item, quantity change, the before and after quantity, reason (opening count, adjustment, receipt, delivery, return), the related transaction line or goods receipt, who and when. On hand never goes below zero. Everything that changes an item's stock first locks that item's row; several items are locked in id order.
- **Unmet demand is a backorder, not negative stock.** Per transaction line: ordered, delivered at completion, backordered, and later fulfillments (each with quantity, who and when). States: waiting for stock, partially fulfilled, ready to fulfill, fulfilled, cancelled.
- **Drafts only warn.** Adding a catalog item shows on hand, available, incoming and the expected shortage; nothing is reserved, and the backend never refuses a line for stock.
- **Completion decides.** Under the item locks, the available quantity is delivered at once (a delivery movement; this business hands products over at the visit) and the shortage becomes a backorder. Separate deliveries for all sales are not built, but the per-line quantities leave room for them.
- **Reopen and cancel undo the effect.** Delivered units come back through a return movement, open backorders are cancelled, and a backorder already fulfilled later is returned too. The original movements are never changed or deleted: the history shows the delivery and the later return. Reopen and cancel of an invoiced transaction stay vetoed by Invoicing, so this applies only before invoicing.
- **Invoicing is separate from fulfillment.** A completed transaction is invoiced in full, backordered units included; the invoice (and later its PDF) may say that units are still backordered. An "invoice only fulfilled items" mode may come later.
- **Incoming stock and receipt.** Incoming stock is recorded separately: item, quantity, expected date, supplier, reference, who and when. A goods receipt is confirmed by a person and turns incoming into on hand through a receipt movement. A receipt never fulfills a backorder by itself: the backlog proposes an oldest-first allocation, and a person confirms it (who and when are recorded).
- **Separate states.** Low stock (below a per-item threshold), out of stock, backordered and incoming are different states, and an item can be in several at once.

### Discounts

Discounts are ordered layers applied one after the other, never added together: base catalog price, then the temporary catalog discount, then the customer's permanent discount (a manual line discount, if added, is one more explicit layer). Each layer stays visible and auditable on the line and on the invoice. **Rounding (decided):** each percentage layer produces a unit price rounded half-up to the currency's two decimals (whole öre for SEK) BEFORE the next layer is applied, in exact `Decimal` arithmetic; the printed steps are the values actually used (100.00, -15% = 85.00, -10% = 76.50). The final unit price then goes through the existing line calculation unchanged: per-line half-up net and VAT, totals as sums of stored line amounts. VAT rounding is not changed by the discount work.

### Ownership limit

`max_owned_organizations` counts owner-role memberships; other memberships never count. Every path to ownership is refused beyond the limit by the backend: creating an organization, a transfer, promotion to owner and accepting an owner invitation. The check lives in ONE core function that every ownership-granting path calls under the receiving user's row lock, with a database backstop so a future path that forgets the function is still refused (the same layering as the last-owner rule). The operator `repair owner` command is the documented administrative override and the only path allowed past the backstop. **An owner invitation accepted at the limit** is refused as a whole with a clear reason ("You cannot become an owner of this organization because you have reached your owned-organization limit."): no silent downgrade to another role, no partial acceptance, and the invitation stays pending until the user frees a slot, their entitlement grows, the inviter changes the role, or it expires or is revoked. Invitations that do not grant ownership are unaffected.

### Recent authentication

Leaving, transferring ownership and deleting an organization require recent authentication: a short-lived proof that the user re-authenticated (by password today). Destructive actions are not tied to passwords, so a future SSO or passkey user re-authenticates in their own way. Changing your password is an account (user) setting, not an organization setting.

---

## Container foundation (D1 as built)

First slice of production-deployment readiness: the two application images and a local, production-LIKE rehearsal.
Nothing is deployed; the readiness endpoint, configuration hardening, CI, backups and runbooks are later slices (D2 to D5).

**Intended production topology** (decided; not built here): Internet -> Coolify proxy (TLS ends here) -> frontend/BFF
(the only public service) -> private FastAPI backend -> private **Coolify-managed PostgreSQL 17**. The browser never
reaches the backend or the database.

**Local rehearsal** (`deploy/compose.rehearsal.yml`) is NOT that topology; it only proves the images: `127.0.0.1:3300 ->
frontend -> backend -> a disposable postgres container on tmpfs`. Only the frontend is published (on loopback); the
backend and the database publish nothing and are reachable by service name only. Its one deviation: the frontend runs
with `APP_ENV=development` because the rehearsal is plain http (production requires an https `PUBLIC_ORIGIN`); the
image tests start the image with `APP_ENV=production` separately.

**Backend image** (`backend/Dockerfile`, context `backend/`): multi-stage; `uv sync --frozen --no-dev` from `uv.lock`
in a builder; the runtime is `python:3.13-slim` with the venv, `app/` (including the checksummed bundled PDF fonts),
`alembic/` and `alembic.ini` (so a SEPARATE one-shot migrate job can run from the same image), no tests, no dev
dependencies, no compilers, no uv/pip, no `.env`, no `seed_dev`/`reset_test_db`. Runs as uid 10001, **one Uvicorn
worker** (decision: do not raise it until the deployment rehearsal has measured memory, Argon2 pressure and database
connections), port 8000. **The web process never migrates**: no startup hook runs Alembic. Container health check:
READINESS (`/health/ready`, since D2; see "Deployment configuration, health and trust (D2 as built)").

**Frontend image** (`frontend/Dockerfile`, context `frontend/`): Node 22, `npm ci`, `next build` with
`output: "standalone"`, then only `.next/standalone`, `.next/static` and `public` are copied into the runtime stage,
which runs `node server.js` as the non-root `node` user (uid 1000) on port 3000. `poweredByHeader` is off. Health check:
LIVENESS only, `GET /api/health` (since D2). The sans font is Noto Sans bundled in `app/fonts` (SIL OFL, byte-identical
to the PDF renderer's fonts): the build contacts no font service.

**One image for every environment (invariant).** Nothing about the deployment is baked into the frontend image. The
authentication mode, environment, public origin and backend address are read from the environment when the server
starts, and every page that depends on them is rendered per request: `/setup`, `/dev-login`, `/login` and `/invite`
declare `dynamic = "force-dynamic"`, the rest read the request's cookies. Before D1, `/setup` and `/dev-login` were
PRERENDERED at build time with the build's `AUTH_MODE`, so an image built without it would have shipped a permanently
404 `/setup` (the first-operator link). `lib/build-config.test.ts` guards the sources; the image tests build the image
under three different build environments and prove the same runtime behaviour from each. The `TEST_BUILD_*` Dockerfile
arguments exist only for that proof; a real build passes none.

**Production fails by exiting.** A standalone Next server only LOGS a failed `register()` and keeps answering 500, which
a connect-only health check would call healthy; `instrumentation.ts` therefore exits the process (status 1) in
production on an unusable authentication configuration.

**Tests:** `cd backend && uv run pytest ../deploy/tests -q` (needs Docker; builds real images, a few minutes; disposable
containers only). They are not part of the ordinary backend suite.

## Deployment configuration, health and trust (D2 as built)

Second slice of production-deployment readiness. Still nothing is deployed: CI (D3), backup/restore tooling (D4) and the
Coolify rehearsal with its runbooks (D5) are later. Everything below is implemented and tested locally; **what only the real
topology can show (the proxy hop count, container replacement semantics) is left to D5 on purpose.**

### Liveness versus readiness (and what a restart means)

| | endpoint | answers | depends on |
|---|---|---|---|
| backend liveness | `GET /health` | `{"status":"ok"}` | nothing (an `async` handler: it never waits for a worker thread) |
| backend readiness | `GET /health/ready` | `200 {"status":"ready"}` / `503 {"status":"unready"}` | the database and its revision |
| frontend liveness | `GET /api/health` | `{"status":"ok"}` | nothing |
| frontend readiness | `GET /api/ready` | `200 {"status":"ready"}` / `503 {"status":"unready"}` | the backend's readiness |

**The one invariant of readiness: the database's Alembic revision == this image's single Alembic head.** `/health/ready` is
ready only if the database is reachable, `alembic_version` exists, holds EXACTLY ONE row and that row EQUALS the code's single
head (read once from the image's migration files). Everything else is unready: no table, an old revision, a newer or unknown
revision (an image older than the database is as unready as a database older than the image: it deliberately does not weaken to
"a revision this code knows"), several rows, several heads in the code, a malformed table, no database. Readiness never
migrates. The response is coarse (`ready` / `unready`); the reason is a short code in the server's log (`not_ready` with
`reason`: `no_revision_table`, `revision_mismatch`, `revision_rows`, `code_heads`, `database_unreachable`, `unexpected_state`,
`check_timeout`, `check_in_progress`), never a revision, host or exception text. The old detailed `/health/db` is gone.

A probe is bounded (4 s) and single-flight: a database that accepts a connection and never answers (a paused or partitioned
server) leaves at most ONE stuck readiness thread, further probes answer unready at once (`check_in_progress`), and because
liveness is `async` it stays instant. **Restart semantics:** nothing in the application exits on an unready answer, and a
temporary database outage makes the backend unready, not dead. The backend IMAGE's health check is readiness (so a deployment
gate cannot call a not-yet-migrated container healthy); the FRONTEND image's health check is liveness only (a backend outage must
not look like the Next process dying). Docker itself does not restart unhealthy containers; **an orchestrator that does must be
pointed at `/health` and `/api/health` (liveness), never at readiness**, or an outage becomes a restart storm. A deployment gate
may additionally ask `/api/ready` for the whole chain. (`/api/ready` is public and costs one backend readiness call per request:
coarse by design; rate limiting it is a proxy concern.) Tests: `backend/tests/test_readiness.py`, and
`deploy/tests/test_orchestration.py` (a backend that never migrated is unready and recovers without a restart when the job runs;
a PAUSED database gives bounded unready answers, no restart, instant liveness and recovery on unpause).

### The migration job

`python -m app.scripts.migrate` (in the backend image) is the ONLY place a production schema changes. It runs with its own
credentials, `MIGRATION_DATABASE_URL` (the schema-owning role), and needs none of the web process's configuration: it has its
own small settings class, and `alembic/env.py` no longer imports the models or `app.core.config` when the job passes its
connection (a module package imports its API, which imports the web settings; the migrations themselves never read the models).
One session does everything:

1. connect; 2. take the fixed PostgreSQL advisory lock (`MIGRATION_LOCK_KEY`) with `pg_try_advisory_lock`, polled: a second job
waits up to `--lock-timeout` (default 600 s), so concurrent jobs serialize, and the lock belongs to the SESSION so it is released
when the session ends however the job ends; 3. read the current revision and REFUSE (exit 4, nothing touched) a database at a
revision this image does not know (a newer release migrated it), a database recording several revisions, or a code base with
several heads; 4. `alembic upgrade head` ON THE SAME CONNECTION (never a downgrade; a dropped connection fails the migration
instead of silently dropping the lock); 5. reconcile the runtime role's grants (below); 6. verify the lock was held throughout.
Exit codes: 0 migrated or already at head, 1 failed, 2 configuration error, 3 lock timeout, 4 refused. Every failure is non-zero
and nothing is swallowed. Output is one JSON line per event with no credential, URL or bound parameter (SQLAlchemy
`hide_parameters`, a scrubber on the one message line that is printed, and a malformed URL is a configuration error that does
not echo it). Production has NO fallback: a missing `MIGRATION_DATABASE_URL` is exit 2, never a silent use of the runtime URL
(development may use `DATABASE_URL`); `RUNTIME_DB_ROLE` is required in production. Not done, by decision: migrating at FastAPI
startup, in every worker, automatic downgrades.

**Rehearsal orchestration** (`deploy/compose.rehearsal.yml`, a LOCAL implementation only; `depends_on` is not evidence of how
Coolify behaves, which is a D5 verification): `postgres (healthy) -> db-bootstrap (one-shot) -> migrate (one-shot, completed
successfully) -> backend (healthy = ready) -> frontend`. Proved locally (`deploy/tests/test_topology.py`,
`test_orchestration.py`): success brings the stack to ready in order (by container timestamps); a REFUSED migration (exit 4) and a
FAILED one (exit 1) leave the backend and frontend never started and nothing answering; the backend never migrates (its logs and
process list); two real concurrent jobs serialize (`backend/tests/test_migrate_runner.py`).

### Database roles (first deployment)

Two roles with distinct credentials, one database:

* **owner / migration role** (`MIGRATION_DATABASE_URL`): owns the database and its schema, runs the migrations, so it owns every
  table, sequence and function. Not a superuser, no CREATEDB, no CREATEROLE.
* **app role** (`DATABASE_URL`, FastAPI at runtime): `SELECT, INSERT, UPDATE, DELETE` on tables and `USAGE, SELECT` on sequences,
  nothing else: no superuser/CREATEDB/CREATEROLE/REPLICATION/BYPASSRLS, no CREATE on the schema, no ownership, no TRUNCATE,
  REFERENCES or TRIGGER, no membership of the owner. It cannot create, alter or drop anything, nor create roles. Triggers and
  `plpgsql` functions run as the caller and functions are executable by default, so ordinary DML needs nothing more (the deferred
  owner-loss trigger, the immutability triggers and the invoice counters all work as this role).

`python -m app.scripts.bootstrap_roles` (a privileged connection, ONCE per database, idempotent, never part of the web process)
creates the two roles, makes the owner own the database and the `public` schema, removes PUBLIC's access, grants the app role
CONNECT and USAGE on the schema, and sets the OWNER's default privileges so objects created by FUTURE migrations are usable by the
app role without a new grant. The migration job then runs a **reconcile** step after every upgrade (as the owner: grants on
everything that exists, the same default privileges, and `REVOKE INSERT, UPDATE, DELETE` on `alembic_version`, so a compromised
app cannot rewrite the revision that readiness trusts). The two mechanisms overlap on purpose (the defaults serve a migration run
by hand, reconcile repairs a database whose defaults were never set) and each has its own test. `test_db_roles.py` also runs about
a thousand ordinary application tests connected as the app role (the few that ARRANGE state by disabling triggers need an owner
and are deselected by name). The production runbook (who runs the bootstrap against the Coolify database, how the two URLs are
provisioned) is D4/D5 material; the architecture and the tests are here.

### Production configuration (fail closed)

**Backend** (`Settings` refuses to start; the error names the rule, never a value: `hide_input_in_errors` is on, and a malformed
`DATABASE_URL` is refused without echoing it). `APP_ENV` has no default. Production requires `AUTH_MODE=session`,
`DATABASE_URL`, `SECURITY_KEY` (>= 32 characters), `PUBLIC_ORIGIN` (https, not localhost, an origin only), `BFF_INTERNAL_SECRET`
(>= 32 characters, >= 8 distinct, different from `SECURITY_KEY`) and `CORS_ORIGINS=[]` (the browser never calls FastAPI; when
empty the CORS middleware is not installed at all). It refuses `AUTH_MODE=dev|disabled`, `DEV_USER_EMAIL` (set at all),
`TEST_DATABASE_URL`, `MIGRATION_DATABASE_URL` (a web process must not hold DDL credentials), a wildcard or localhost CORS origin
and a missing/http/localhost `PUBLIC_ORIGIN`. Development keeps its conveniences (a localhost origin default, the dev CORS origin,
no secret).

**Frontend** (`instrumentation-node.ts` exits with status 1 in production, printing only the reasons): explicit
`APP_ENV=production`, `AUTH_MODE=session`, `PUBLIC_ORIGIN` https, `BACKEND_URL` present and a **private/internal** destination,
`BFF_INTERNAL_SECRET` (same strength rule) and a valid `TRUSTED_PROXY_HOPS` if set. Production never falls back to
`http://localhost:8000`. The `BACKEND_URL` rule (defensible, and honest about what it cannot see): `http(s)`, an origin only, no
credentials, and the host is (a) a private IP literal (10/8, 172.16/12, 192.168/16, fc00::/7; loopback, link-local (cloud
metadata) and public addresses are refused), or (b) a single-label name (a Docker/Coolify service name resolves only inside the
network), or (c) a dotted name ending in `.internal`, `.local`, `.lan`, `.localdomain`, `.home.arpa`, `.svc` or `.cluster.local`;
any other dotted name could be a public DNS name and is refused. It cannot see what a name resolves to: network isolation stays
mandatory.

### BFF -> FastAPI internal authentication

A shared secret (`BFF_INTERNAL_SECRET`, header `x-bff-secret`) is sent by the BFF on EVERY upstream request (`backendFetch` is the
only place that talks to FastAPI, and a static test keeps it so) and checked by FastAPI BEFORE routing, sessions or bodies, with
`hmac.compare_digest`. A request without it, or with a wrong one, gets the same fixed `403
{"detail":{"code":"internal_auth_failed"}}` plus an `x-internal-auth: rejected` header (which lets the BFF tell "our configuration
is wrong" from an application 403). **It does not replace private networking**; it makes a request that arrives from elsewhere
useless. **Decisions:** `/health` and `/health/ready` are exempt (coarse and private, so an orchestrator can probe them without the
secret); every other HTTP route, including docs/openapi, unknown paths and the pre-auth login/setup/invite routes (a user has no
session yet, but the BFF authenticates ITSELF), requires it. **Compatibility:** enforcement follows the setting. In production it
is required on both sides (startup refuses otherwise); in development it is optional on both: when set it is enforced (the session
Playwright run and the rehearsal exercise the real chain), when unset nothing is checked (the dev run, the backend unit tests).
Rotation is a redeploy of both services with the new value (no dual-key window yet; a short period of 502s is the cost). The
secret is never logged, returned, in a URL or a bundle, or `NEXT_PUBLIC_*`; a browser's copy of the header (and of any trust
header) is dropped because upstream headers are built from scratch.

### Request id

The BFF generates one 128-bit random id per request (32 hex characters); whatever the browser sent is ignored. It is forwarded as
`x-request-id`, logged by both services and returned to the browser. FastAPI accepts an incoming id only if it matches
`^[A-Za-z0-9_-]{16,64}$` (anything else is replaced), so no client text, control character or long string reaches a log. It is a
correlation key, not a secret and not an authority. A server-rendered page's upstream calls get their own id per call.

### Client address and proxy trust (disabled until D5)

`TRUSTED_PROXY_HOPS=0` (frontend) and `TRUST_CLIENT_IP_HEADER=false` (backend) stay the defaults: with trust off the browser's
address is NOT known, FastAPI throttles by the BFF's address, and the S1 trade-off (per-source budgets shared by all users) still
applies. The mechanism is implemented and tested so that D5 only sets numbers. With N trusted proxies that each append the address
they saw to `X-Forwarded-For`, the BFF takes the entry N places from the RIGHT (never anything the client wrote on the left),
refuses to guess when there are fewer entries than hops or the entry is not an IP (ports, scripts, empty entries and malformed
values all give nothing), treats separate header lines as one list, and sends ONLY the derived value in `x-client-ip`; the
browser's own `x-client-ip`, `x-forwarded-*`, `x-real-ip` and `forwarded` are never forwarded. FastAPI believes `x-client-ip` only
when `TRUST_CLIENT_IP_HEADER=true` AND the request passed the internal-secret check (so that setting also requires the secret), and
unwraps IPv4-mapped IPv6 addresses (`::ffff:a.b.c.d` is that IPv4 client, not one /64 shared by all of them). **D5 determines the
real hop count from the real Coolify/Traefik chain; nothing is guessed here.**

### Logging and redaction

One JSON object per line on stdout from both services (`ts`, `level`, `service`, `event`, ...). A request line has exactly the
request id, method, **path without the query string**, status and duration. Never a header, cookie, body, token, secret or
exception message: errors are logged by CLASS (a fetch failure's message can name the backend host) and the one message the
migration job prints is scrubbed. Uvicorn's own access log is disabled (it prints the whole request target, query string included,
and would duplicate the safe line): `--no-access-log` in the image and a filter in the application; likewise `--no-proxy-headers`
and `--no-server-header`. SQLAlchemy engines (application and migration) use `hide_parameters=True`, so an exception's text carries
the SQL but not the bound values. The frontend wraps every API route in `instrument()` (a static test fails an unwrapped route).
Not built, on purpose: metrics, tracing or log aggregation. A known consequence: probes (`/health` every 15 s) are logged like any
request.

### Security headers and HSTS

Every response carries `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`, `X-Frame-Options:
DENY` and a conservative `Permissions-Policy` (`next.config.ts`, first in the list so that later rules win); the login, setup and
invite pages and the auth/invite APIs keep their STRONGER `no-referrer` and `no-store`. `X-Powered-By` is off. **No
Content-Security-Policy** (tracked: report-only first). The backend's own responses carry `nosniff`, `X-Frame-Options`,
`Referrer-Policy: no-referrer` and `x-request-id`.

**HSTS decision: option B, emitted by the application** (`proxy.ts`, per request from the runtime configuration, because a static
header would bake a value into an image that is built once): `Strict-Transport-Security: max-age=31536000` ONLY when
`APP_ENV=production` and `PUBLIC_ORIGIN` is https; never on development or plain http. **No `includeSubDomains`** (we do not
control every subdomain of the host) and **no `preload`**. Why not leave it to the proxy (option A): the application knows its own
canonical origin, and the value is testable here and cannot be forgotten by a proxy setting; the cost is one more thing the proxy
must not duplicate with a different value (D5 checks the real response). A browser honours HSTS only over TLS.

### The BFF failure contract

When FastAPI cannot answer, the browser gets a FIXED coarse answer with a stable code: a refused connection or DNS failure `502
{"detail":"Backend unavailable","code":"upstream_unavailable"}`, a timeout or abort `504 ... "upstream_timeout"`, any upstream 5xx
`502` (a 503 stays a 503 with `Retry-After`), and FastAPI refusing the BFF's own secret a `502` (plus a loud log line for the
operator: it is OUR misconfiguration). Never a hostname, stack, connection string, driver message or raw fetch error. An ordinary
application answer (400/403/404/409/412/422/429) is NOT an infrastructure failure and passes through unchanged. A database that
cannot be reached becomes a fixed `503 service_unavailable` from FastAPI itself.

### Deployment debt carried forward (explicit, not built)

* **Scheduled purge** of security/auth events (`SECURITY_EVENT_RETENTION_DAYS`, `AUTH_RECORD_RETENTION_DAYS`): the purge functions
  exist; running them on a schedule is a deployment task.
* **Invitation preview and account-creation throttling by source** needs the verified client address, so it waits for D5. Until
  then 256-bit tokens make guessing infeasible and Argon2 admission bounds the cost, as in S5.
* CSP (report-only first); a dual-key rotation window for the internal secret; probe-log noise; a cache in front of `/api/ready`.

### Verification, trade-offs and deviations (D2)

**Verified (all on disposable databases and containers; the development database stayed at `e29c5d7a3b48`, row-for-row):**
backend `pytest` 2788 passed (6 skipped as before); frontend typecheck and lint clean, Vitest 2241 passed (81 files), production
build; Playwright dev run 358 passed twice in a row, session run (the BFF secret enforced end to end, plus `session/hardening.spec.ts`)
282 passed twice in a row; container suite (`deploy/tests`: images, topology, orchestration, D2 images) 59 passed; `alembic check`
reports no schema drift (D2 adds no migration). **Fault injection:** 77 single-fault mutations (47 backend, 30 frontend; each applied
to the real source, the targeted tests run, the file restored), every one killed, none survived: readiness accepting an old, newer
or unknown revision, several rows, or a missing table; migration without the lock, a swallowed failure, an unknown revision not
refused, the migration imported by the web startup, no grant reconciliation, a leaking scrubber; every unsafe production value;
internal auth not validated, non-constant-time, the probe exemption widened, a forged client address accepted, the guard not
installed; the query string, the secret, Authorization or a browser request id logged or trusted (both services); `hide_parameters`
removed (both engines); the app role given DDL, superuser, membership of the owner, or write access to `alembic_version`; no default
privileges, or no reconcile; the Uvicorn access log or flags restored; the image health check or command changed; the BFF not sending
or trusting the secret, forwarding a browser header, an unwrapped route; a leaked hostname, a relayed 5xx, 4xx treated as
infrastructure; HSTS on development or with `includeSubDomains`/`preload`, weakened secret-page headers, a CSP slipping in. Joint faults
were used where redundancy is intentional: the CORS default AND its checks (C1), both `AUTH_MODE=dev` checks (C2). The two
grant mechanisms (bootstrap defaults and the job's reconcile) are separate faults killed by separate tests, because either alone
keeps a real system working. Every mutation was applied to a baseline that passes (non-vacuous), and a mutation that did not apply
would have been reported as such (none).

**Trade-offs and deviations to know about:**

* The backend image's health check is readiness and the frontend's is liveness: this deliberately differs from "one health check
  per image". Docker does not restart unhealthy containers; an orchestrator that does must use liveness.
* `APP_ENV` has no backend default any more (a process that was not told its environment does not start); the existing tests set it.
* Production refuses ANY non-empty `CORS_ORIGINS` (stricter than "no wildcard, no localhost"), and `DEV_USER_EMAIL` counts as
  configured even when empty.
* The rehearsal backend's `PUBLIC_ORIGIN` is a placeholder https origin (production rules) while its frontend is `APP_ENV=development`
  over plain http; the rehearsal proves orchestration, not TLS.
* `BACKEND_URL` is validated by name or address only; what a service name resolves to is network isolation's job.
* `/api/ready` is public and uncached (one backend readiness call per request); the request log also records every probe.
* The internal secret has no dual-key rotation window; rotation briefly yields 502s.
* A database that cannot be reached during an ordinary request is now a fixed 503 from FastAPI (before: an unhandled 500).
* `alembic/env.py` loads no model metadata for the migration job (`alembic check` and autogenerate still load it, and then need the web settings too).
* The role-restricted test run deselects four tests that arrange state by disabling triggers (they need an owner by nature).
* A pre-existing thread warning in the concurrency suite (`BrokenBarrierError` in a barrier helper) still appears in some full runs (it was already there in the first full run of this slice, before any
  D2 concurrency test existed); the barrier helper is older code.
* HSTS is emitted by the application (option B); D5 must confirm the proxy does not add a second, different value.


## Continuous integration (D3 as built)

Third slice of production-deployment readiness. GitHub Actions encodes the already-approved application, security, migration
and container invariants as gates. **Nothing is deployed, no repository or deployment secret exists, and no workflow can reach the
development database, staging, production, a backup or Coolify:** every job runs on a fresh runner against infrastructure it
creates itself, and static tests (below) fail if that changes. The files are in `.github/`; the workflow tests are
`backend/tests/test_ci_workflows.py`.

### Job graph and triggers

`ci.yml` runs on every pull request, every push to `main` and on demand. All jobs run in parallel (wall time is the slowest job,
not the sum); `ci-gate` waits for all of them.

| job | what it proves | measured locally* |
|---|---|---|
| `workflows` | actionlint (with shellcheck) over every workflow | seconds |
| `backend` | frozen `uv` install on Python 3.13 (the image's version); the full pytest suite on a disposable PostgreSQL 17, split into three visible steps: everything else (this includes the architecture, boundary and workflow tests), the migration job (`test_migrate_runner.py`: real processes, advisory-lock concurrency, refusal, failure) and the restricted runtime role (`test_db_roles.py`: DDL denied, future objects usable, ~1000 application tests connected AS the app role); then a fresh database migrated by the ACTUAL job (`python -m app.scripts.migrate`), `alembic upgrade head` and `alembic check` | ~7 min (4:37 + 0:32 + 1:05 + migration step) |
| `frontend` | `npm ci`, typecheck, lint, all of Vitest, the production build with NO configuration (no `AUTH_MODE`, `BACKEND_URL`, secret or font service) | ~3 min |
| `containers` | builds BOTH production Dockerfiles from a clean runner (no layer cache) and runs the whole D1 + D2 suite: non-root users, one worker, PDF and fonts, standalone runtime, only the intended ports, the migration orchestration (success, refusal, failure), exact-head readiness, the BFF internal secret, the restricted runtime role, a paused database | ~4.5 min |
| `e2e-dev` | the full Playwright suite with the dev identity (Chromium) | ~8 min |
| `e2e-session` | the full real-session Playwright suite: real login, the BFF internal secret enforced end to end, tenant behaviour, invitations, `hardening.spec.ts` (Chromium) | ~8 min |
| `ci-gate` | green only if EVERY job above succeeded (skipped or cancelled counts as failure) | seconds |

\* local single-machine times from clean-checkout runs (below); hosted 2-vCPU runners are expected to be 1.5 to 2 times slower, which the job timeouts allow for
(see "Timeouts"). Real runner timings are an open item until the first runs exist.

**Why all five heavy jobs run on pull requests too, not only on `main`:** they are parallel (no extra wall time), and a gate that
only runs after merge cannot stop the merge. If runner minutes become a constraint, the first lever is to move `e2e-dev` and
`e2e-session` to `main` plus a label or path filter; the aggregate `ci-gate` keeps the required-check name stable either way.

**Concurrency.** A newer commit on the same pull request cancels the obsolete run. A push to `main` gets a concurrency group of its
own (the run id), so no later push can cancel or replace a main run, and no required gate is hidden. Concurrent runs are
separate virtual machines, so they cannot share a Docker daemon; inside a job every Compose project has a unique name
(`bp-ci-<run>-<attempt>-<label>`, one label per job) and the container tests name their stacks per session.

### Reproduced from a clean checkout (what that found)

Every job was run locally from a copy of the tree that contains exactly what a commit would (`git ls-files -co --exclude-standard`: no
`.env`, no `node_modules`, no `.venv`), the job's own `run:` steps executed in bash against a freshly started disposable
database, on Python 3.13 for the backend. That found four hidden dependencies on a developer's machine, all fixed: nine backend
tests relied on a developer's `.env` (`AUTH_MODE=dev`, a known development database URL); `reset_test_db` (Playwright's global
setup) needed an `APP_ENV` that only `.env` supplied; plain `alembic upgrade` loaded the web settings (it now needs only the
migration credentials; `alembic check` and `revision` still read the models and so also need `DATABASE_URL`); and one Playwright
spec queried the development database service, which does not exist in CI (it now asserts that none is running). Not reproducible
locally: Chromium (its download is blocked on the development machine), so those runs used the installed Edge through the same
`E2E_BROWSER` mechanism; the Chromium install and launch will first be exercised by GitHub.

### Disposable infrastructure only

* **The database.** `.github/actions/disposable-db` starts the repository's own `postgres-test` service (PostgreSQL 17, tmpfs,
  loopback port 5433: the same service developers use) with a freshly generated, masked password, waits for it with a bounded
  `--wait-timeout 90`, and exports `TEST_DATABASE_URL`. The database is named `business_platform_test`; the privileged role is
  `ci_admin`. Everything vanishes with the runner.
* **The guard.** `.github/scripts/assert_ci_database.py` (standard library, runs right after the server is up) refuses unless
  `TEST_DATABASE_URL` is the only database URL in the environment, points at the runner's loopback on port 5433, names a `*_test`
  database, no `DATABASE_URL`/`MIGRATION_DATABASE_URL`/`BOOTSTRAP_DATABASE_URL`/`PG*` variable exists (no fallback to anywhere
  else) and no `.env` file is in the checkout. It never echoes a URL or value. The existing guards (`reset_test_db`, the Playwright
  `assertTestDatabase`) remain and still apply. No real `DATABASE_URL` appears in any workflow (a static test).
* **The restricted role.** The role proof never uses a superuser as evidence: `test_db_roles.py` creates its own owner and app
  roles, and the child run's `conftest.py` refuses to start (exit 2) unless its connection is a role that is not a superuser,
  creator, table owner, database owner or schema-CREATE holder; a test hands it the owner's and the superuser's URL to prove the refusal.

### Generated credentials (no repository secrets)

Nothing is stored in GitHub. The database password and, for the session run, `E2E_BFF_SECRET`, `E2E_SECURITY_KEY` and
`E2E_PASSWORD` are generated per job with `openssl rand`, registered with `::add-mask::` BEFORE they are written to `GITHUB_ENV`,
and never echoed; `set -x` is forbidden by a test. The Playwright support code reads those variables and falls back to its
throwaway local defaults only outside CI. The container suite generates its secrets per test session. The backend unit tests use
fixed sentinel constants that configure only in-process test instances, never a service.

### Browser

Playwright drives **Chromium** installed by `npx playwright install --with-deps chromium` (exactly that one browser and its
system libraries) and selected through the existing `E2E_BROWSER` mechanism, set at job level. In CI there is no fallback: an
unset `E2E_BROWSER` throws (a runner has no Microsoft Edge) instead of silently asking for a browser that is not there. No
assertion was weakened. Traces and screenshots are off in CI (they hold cookies, tokens and one-time links).

### Caching

`setup-uv` caches by `backend/uv.lock`; `setup-node` caches npm by `frontend/package-lock.json`. Nothing else: no Playwright
browser cache (installed fresh, a minute), no Docker layer cache (the container job is a clean build by design), and never a
`.env`, database, session, credential or token. A test forbids `actions/cache` and every cache-from/to setting.

### Artifacts

Only on failure, only `ci-logs/*.log`, retained 7 days: the job's own test output after it has passed through
`.github/scripts/redact.py`, which replaces database credentials, `Authorization`/`Cookie`/`Set-Cookie` values, the BFF, CSRF
and pre-auth headers, the session/CSRF/dev cookies, JSON secret fields, one-time `/setup#` and `/invite#` links and any bare
43-character token or long hex secret by `[redacted]`. The same filter sits in front of the console copy. No trace, screenshot,
cookie jar, storage state, database dump or `.env` is ever uploaded (a test fixes the exact artifact path and requires the filter
on every line that writes a log).

### Required checks (recommendation; branch protection is NOT configured by D3)

* **Require on pull requests to `main`:** the single aggregate **`ci-gate`** (it is green only if all of `workflows`, `backend`,
  `frontend`, `containers`, `e2e-dev` and `e2e-session` are). One name keeps protection stable when jobs are added or renamed.
  Equivalent, less maintainable: require those six individually. Also: require branches to be up to date, and disallow bypass.
* **Main/deployment-quality:** the same run on every push to `main`; a red `main` blocks a deployment (D5).
* **Scheduled, informational (never required):** `audit.yml`.

### Scheduled audits

`audit.yml` runs Mondays 05:17 UTC and on demand, never on pull requests: `pip-audit` over the locked production dependencies,
`npm audit --omit=dev` (what the image ships) plus a full `npm audit` reported informationally, and Trivy on both production
images (HIGH and CRITICAL with a fix available). **Baseline reviewed for D3 (2026-10-06):** `pip-audit` 0 findings; `npm audit
--omit=dev` 0; full `npm audit` 5 HIGH, all in the development lint chain (`eslint-config-next`, `@next/eslint-plugin-next`,
`fast-glob`, `micromatch`, `braces`: stack-exhaustion in glob patterns; npm's "fix" is a downgrade to `eslint-config-next@14`,
which is wrong for Next 16) and not in any image; Trivy backend image 1 HIGH (Debian `libpcre2-8-0`, fixed in
`10.46-1~deb13u3`); Trivy frontend image 2 HIGH OS packages (`libpcre2-8-0`, `perl-base`) and 10 HIGH in the npm CLI that
`node:22-slim` bundles under `/usr/local/lib/node_modules/npm` (`brace-expansion`, `ip-address`, `pacote`, `picomatch`,
`sigstore`), none in the application's own `node_modules`. **Proposed policy (for review):** the dependency audits fail the
scheduled run on any production finding (they are clean today); the dev-tooling audit stays informational; Trivy stays at
`exit-code: 0` (findings visible in the log, run green) until this baseline is accepted, then flips to `1`. The frontend image's
npm findings disappear if the runtime stage deletes the unused npm CLI, and the Debian ones with a base-image refresh; both are
Dockerfile changes left out of D3 on purpose. A new finding becomes a TODO item, not a blanket ignore.

### Supply chain

Action pinning policy: only maintained publishers (`actions/*`, `astral-sh/setup-uv`, `aquasecurity/trivy-action`), **every
action pinned to a full commit SHA with a `# vX.Y.Z` comment** (immutable, unlike a movable major tag); a weekly Dependabot
configuration (`.github/dependabot.yml`, GitHub Actions only) proposes updates. A test enforces owner, SHA and comment.
Tool images run by workflows carry an explicit version tag (`rhysd/actionlint:1.7.7`); the workflows have `contents: read`
only, checkouts do not persist credentials, and there is no `pull_request_target`, `workflow_run`, self-hosted runner or secret.
**Docker base images remain FLOATING TAGS** (`python:3.13-slim`, `node:22-slim`, `postgres:17`; the `uv` image is pinned to
`0.12.23`): digest pinning (with an automated refresh) is recommended as a pre-production hardening task, together with the
Trivy flip above; it was not changed in D3. Unauthenticated Docker Hub pulls from shared runners can be rate limited; the
container job would then fail loudly rather than pass.

### Timeouts

Explicit job limits sized at roughly three times the local measurement: `backend` 30 min, `frontend` 20, `containers` 40, each
Playwright job 40, `workflows` and `ci-gate` 5. A hung migration, concurrency test or browser run is killed by its job limit.

### Restore boundary

D3 contains no backup, dump or restore step. The backup format, the verification script, the latest-backup restore drill and the
runbook belong to D4; no production backup ever enters GitHub Actions.

### Reproduce each job locally

(From the repository root; `docker compose up -d postgres-test` is the same service CI uses. A local `.env` is fine locally;
CI's guard refuses one on a runner.)

* **backend:** `cd backend && uv sync --frozen`, then `uv run pytest -q --ignore=tests/test_migrate_runner.py --ignore=tests/test_db_roles.py`,
  `uv run pytest -q tests/test_migrate_runner.py`, `uv run pytest -q tests/test_db_roles.py`; the fresh-database step: create a
  throwaway `*_test` database on the test server, then with `APP_ENV=development MIGRATION_DATABASE_URL=<its url>` run
  `uv run python -m app.scripts.migrate`, `uv run alembic upgrade head`, `uv run alembic check`.
* **frontend:** `cd frontend && npm ci && npm run typecheck && npm run lint && npm run test && npm run build`.
* **containers:** `cd backend && uv run pytest ../deploy/tests -q` (needs Docker).
* **e2e-dev / e2e-session:** `cd frontend && npx playwright install --with-deps chromium`, then `E2E_BROWSER=chromium npm run test:e2e`
  and `E2E_BROWSER=chromium E2E_BFF_SECRET=... E2E_SECURITY_KEY=... E2E_PASSWORD=... npm run test:e2e:session` (any throwaway
  values; locally `E2E_BROWSER=msedge` or `chrome` uses an installed browser).
* **workflows:** `docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:1.7.7`.
* **audits:** `cd backend && uv export --frozen --no-dev --no-hashes --no-emit-project -o req.txt && uvx pip-audit -r req.txt --disable-pip --no-deps`;
  `cd frontend && npm audit --omit=dev`; Trivy: `docker run --rm -v /var/run/docker.sock:/var/run/docker.sock aquasec/trivy image --severity HIGH,CRITICAL --ignore-unfixed <image>`.


## Backup and restore (D4 as built)

Fourth slice of production-deployment readiness. **The invariant: a backup is not verified until it has been restored into a
separate, isolated database and the restored state has been checked.** Everything here was built and run against disposable
databases and containers only; nothing is deployed, no storage is configured, and the application's business behaviour is untouched.
Operator-facing procedures are in `docs/backup-restore.md` (commands, guards, roles, drill, disaster recovery) and
`docs/deployment.md` (topology, configuration, migration gating, first operator, rollback); this section records the decisions.

### Decisions

* **Logical, whole-database, custom format** (`pg_dump --format=custom`, PostgreSQL 17 client in the backend image). Nothing is selected
  or excluded: auth records and `invoice_pdfs` are inside the dump because the system cannot be recovered without them. Not built, by
  decision: WAL archiving/PITR, a scheduler, off-host upload, encryption, an automatic pre-migration backup.
* **RPO is up to 24 hours** (nightly dumps), an accepted limitation. **No RTO is promised**: the drill's restore time is a measured
  baseline on a tiny database. Upgrade paths: more frequent dumps, WAL/PITR, a managed provider.
* **Each tool takes its database from ONE explicit variable and never from `DATABASE_URL`** (`BACKUP_DATABASE_URL`,
  `RESTORE_DATABASE_URL`, `VERIFY_DATABASE_URL`, `STATS_DATABASE_URL`). `app.core.pgtools` is the shared helper: URL parsing that
  never echoes a malformed URL, client programs run with the credentials in `PGPASSWORD` (never in argv) and without any ambient `PG*`
  variable, and a scrubber that removes the known secrets, any URL and libpq's `connection to server at ...` text from whatever a client prints.
* **The backup is consistent with its manifest:** one `REPEATABLE READ` read-only transaction collects counts and exports a snapshot
  that `pg_dump --snapshot` uses. The dump is written to `.partial`, proved readable (`pg_restore --list`), hashed, then published with a
  hard link that cannot replace a file; the manifest the same way. A failure removes everything the run created (a dump whose manifest
  cannot be written is removed too).
* **Role model: the dump carries no owner and no ACL** (`--no-owner --no-acl`). The restore connects as the target environment's owner
  role (never a superuser, never the runtime role), so everything is owned by it, and then runs the SAME `reconcile_runtime_grants`
  the migration job runs, which gives the runtime role DML and nothing else. Same role names or different ones: one deterministic
  result, no password in a dump or a runbook, and the runtime role is never made powerful to make a restore work. `bootstrap_roles`
  gained an optional third, read-only **backup role** (`pg_read_all_data`).
* **The restore refuses unless the target is a scratch database**: the name must match `^[a-z][a-z0-9_]*_restore(_[a-z0-9]+)?$` whatever the
  host, must not be the manifest's source nor any configured source URL, must be owned by the connecting non-superuser role, and must be
  empty unless `--reset-target` (which drops only the target's `public` schema). The dump must match its manifest (size and SHA-256).
  `pg_restore --single-transaction --exit-on-error`: a failure leaves the target empty and exits non-zero. Recovery is never in place.
* **`verify_restore` is read-only** (a read-only connection even for a role that could write) and checks: exactly one revision equal to
  the code's head and optionally `alembic check`; the triggers and functions the guarantees rest on; every constraint validated;
  tenant `organization_id` NOT NULL; counts against the manifest; every stored PDF (bytes present, length, SHA-256, `%PDF-`); auth record
  presence and shape; and, with the runtime role's own credentials, DML yes / DDL denied (a real `CREATE TABLE`, rolled back). The
  verifier's lists are tied to the migrated schema by a test, so a new trigger, function or tenant table cannot be forgotten.
  `--fingerprint` makes "the restore equals the source" and "the source did not change" comparable facts.
* **Finding, recorded:** the schema's own CHECK constraints already refuse a PDF whose hash, size or signature disagrees with its
  bytes, and `pg_restore` applies them while loading. The verifier's PDF checks are therefore a second, independent line (for a restore
  made with constraints skipped, or damage the database never validated); their tests drop the CHECK first so the verifier is the only
  barrier being tested.
* **The drill is automated** (`deploy/drill/test_restore_drill.py`): two disposable PostgreSQL 17 servers, the real production backend
  image (which now carries `postgresql-client-17`), a source with representative data built through the application as the restricted
  role, a restore into a separate server with different role names, verification, the real backend served from the restored
  database as the restricted role (readiness, a session created before the backup, tenant reads, a foreign invoice = 404, the PDF
  byte-for-byte equal to the frozen one), restored fingerprint equal to the source's, source unchanged, and a damaged dump that fails in
  `pg_restore` leaving its target empty. It lives outside `deploy/tests` so the merge-gating container job does not contain it; it runs
  from `.github/workflows/restore-drill.yml` (on demand and weekly), never as a gate, never with a real backup or credential.

### Verification, trade-offs and deviations (D4)

**Verified (disposable databases and containers only; the development database stayed at `e29c5d7a3b48`, row-for-row):** backend
`pytest` 2950 passed (6 skipped as before: 2904 main, 18 migration job, 28 restricted role), container suite 59 passed, restore drill 11
passed; `actionlint` clean on the new workflow. **Fault injection:** 56 mutations of the real source (tools, scrubber, role bootstrap,
runbooks, and the drill itself), each applied to a baseline that passes and the file restored: 51 killed outright. Five single faults
survived, all by intentional redundancy, and each is killed by its joint fault: the early existence check AND the atomic no-overwrite
publish (B4a was in fact killed alone, because the early check is the only guard for a leftover manifest or partial file; only the
atomic publish alone survives); the verifier's real `CREATE TABLE` probe and its privilege check (V6); its `read_only` flag and its
`default_transaction_read_only` option (V7). Trade-offs to know about:

* The tool-logic tests use a fake `pg_dump`/`pg_restore` (`backend/tests/fake_pg.py`) so they run anywhere; only the drill runs the real clients.
* `bootstrap_roles` resets the password of a role that already exists (it always did); on a server that already hosts the production roles, give it their current passwords.
* A recovered database may keep its `..._restore_...` name; renaming needs a moment with no connections.
* There is no bulk session-revocation command; after a compromise (as opposed to a failure) a restore brings back the sessions of the backup moment.
* The pre-migration backup is a documented contract, not code; D5 proves the orchestration.
* One new static-test count changed: the CI test that pins the number of artifact uploads now expects five (the drill workflow's failure log, under the same rules).
