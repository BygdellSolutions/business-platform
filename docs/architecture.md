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

**Boundary.** A domain module (`app/modules/<name>/`) may import core and the generic modules (customers, catalog). Nothing else may import it or mention its concepts. Only `app/main.py` (mounts the router), `alembic/env.py` (imports the models) and the dev seed know a module exists. `tests/test_module_boundaries.py` fails if core, Customers or Catalog mention horses. There is no `customer.horses`; use `GET /api/horses?owner_customer_id=...`. Per-organization enabling of modules is not built yet.

**Tenant-safe references (three layers).**
1. API: `resolve_reference(db, ctx, Model, id, field)` (`app/core/tenant_scope.py`) checks the id exists in the active organization. A foreign id and a nonexistent id give the same 422 on the field (`reference.not_found`), so references cannot probe other tenants. Inactive records are refused for new assignments (`reference.inactive`); an unchanged existing reference stays valid after the target is deactivated.
2. Database: composite foreign keys `(organization_id, <ref>_id) -> target(organization_id, id)`, which need `UNIQUE (organization_id, id)` on the target table (added to `customers`; add it to any table that other tables will reference). The database itself refuses a cross-tenant link.
3. Tests: the shared tenant contract plus module-specific reference tests, including direct SQL that bypasses the API.

**Deleting referenced records.** References use `ON DELETE RESTRICT`. `delete_or_409` (`app/core/query.py`) turns the foreign-key violation into `409` with a generic message that does not name the referencing module. Deactivate (`active=false`) instead.

**Owner, stable and billing are separate.** `Horse.owner_customer_id` and `Horse.stable_customer_id` are independent Customer references; neither implies the other. The billing customer belongs to the future transaction, never to the horse.

**Owner -> Horse filtering later (section 12).** The horse list already filters by `owner_customer_id` and `stable_customer_id`. A future UDF reference field is configuration data (source `horses`, `depends_on` owner, filter parameter `owner_customer_id`); Sales and the UDF engine need no horse-specific code. When UDFs arrive, modules register their reference sources in a small registry. Polymorphic references cannot use composite foreign keys, so they are validated with `resolve_reference` instead.

---

## Implementation notes: sales / transactions

Implemented in `app/modules/sales/`. Sales is industry-neutral: it imports core, Customers and Catalog only, never a domain module, and nothing but the wiring files may import Sales (both directions are enforced by `tests/test_module_boundaries.py`).

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
