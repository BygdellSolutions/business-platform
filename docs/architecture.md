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
- item_type       # service | product
- name
- description
- unit
- price
- vat_rate
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
- owner_customer_id
- stable_customer_id (optional)
- birth_year (optional)
- sex (optional)
- breed (optional)
- notes (optional)
- created_at
- updated_at
```

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

