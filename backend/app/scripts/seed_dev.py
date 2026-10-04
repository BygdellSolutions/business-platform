"""Seed development data. Safe to re-run. Refuses to run outside APP_ENV=development.

    python -m app.scripts.seed_dev

Layout (chosen to exercise tenant selection and negative membership cases):

    fredrik@dev.test  owner of Fredrik Horse Therapy, admin of Umeå Stable Services
    maria@dev.test    employee of Umeå Stable Services only

Both organizations have a customer named "Anna Andersson", an item named
"Horse massage", a horse named "Kalle" and a completed transaction for one
"Horse massage" (identical-looking data in different tenants), which is what
isolation checks should be run against.

Each organization also configures two custom fields on transaction lines, Owner (a
customer) and Horse (a horse, narrowed to the selected owner), and the seeded line
carries Anna Andersson / Kalle in them.

The seeded organizations EXPLICITLY use SEK (their default currency and the currency of their
seeded transactions) and have a small business profile. That is seed data choosing a currency
for its own organizations; the platform itself assumes none. A value that is already set is
never overwritten, so re-running the seed keeps what a person edited.
"""

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import SessionLocal
from app.models import (
    Customer,
    CustomerType,
    Item,
    ItemType,
    Organization,
    OrganizationUser,
    Role,
    User,
)
from app.modules.custom_fields.models import CustomFieldDefinition, CustomFieldValue
from app.modules.equine.models import Horse
from app.modules.sales.models import Transaction, TransactionLine, TransactionStatus
from app.modules.sales.pricing import calculate_line

# Fixed ids make the seed idempotent and let docs/tests refer to known tenants.
ORG_HORSE_THERAPY_ID = uuid.UUID("00000000-0000-4000-8000-0000000000a1")
ORG_STABLE_SERVICES_ID = uuid.UUID("00000000-0000-4000-8000-0000000000b2")


@dataclass(frozen=True)
class SeedUser:
    email: str
    name: str
    memberships: tuple[tuple[uuid.UUID, Role], ...]
    # An account property (NOT implied by any role); Maria deliberately has it off so both cases exist in dev data.
    can_create_organizations: bool = False


ORGANIZATIONS = {
    ORG_HORSE_THERAPY_ID: "Fredrik Horse Therapy",
    ORG_STABLE_SERVICES_ID: "Umeå Stable Services",
}

USERS = (
    SeedUser(
        "fredrik@dev.test",
        "Fredrik (dev)",
        ((ORG_HORSE_THERAPY_ID, Role.OWNER), (ORG_STABLE_SERVICES_ID, Role.ADMIN)),
        can_create_organizations=True,
    ),
    SeedUser("maria@dev.test", "Maria (dev)", ((ORG_STABLE_SERVICES_ID, Role.EMPLOYEE),)),
)


# (organization, name, type, email) - identified by organization + name so re-runs add nothing.
CUSTOMERS = (
    (ORG_HORSE_THERAPY_ID, "Anna Andersson", CustomerType.PERSON, "anna@example.test"),
    (ORG_HORSE_THERAPY_ID, "Umeå HK", CustomerType.COMPANY, "info@umea-hk.example.test"),
    (ORG_STABLE_SERVICES_ID, "Anna Andersson", CustomerType.PERSON, "anna@example.test"),
)


# (organization, name, type, unit, price excl. VAT, VAT %) - identified by organization + name.
ITEMS = (
    (ORG_HORSE_THERAPY_ID, "Horse massage", ItemType.SERVICE, "session", "850.00", "25.00"),
    (ORG_STABLE_SERVICES_ID, "Horse massage", ItemType.SERVICE, "session", "850.00", "25.00"),
)


# (organization, name, owner customer, stable customer or None, birth year, sex, breed)
# Owner and stable are looked up by customer name within the same organization.
HORSES = (
    (ORG_HORSE_THERAPY_ID, "Kalle", "Anna Andersson", "Umeå HK", 2015, "gelding", "Swedish Warmblood"),
    (ORG_STABLE_SERVICES_ID, "Kalle", "Anna Andersson", None, 2015, "gelding", "Swedish Warmblood"),
)


# (organization, billing customer name). One completed transaction with one line, copied
# from the organization's "Horse massage" item. Who owns which horse is NOT recorded here:
# Sales does not know about it (that context belongs to the future custom-field mechanism).
SEED_TRANSACTION_DATE = date(2026, 10, 1)
TRANSACTIONS = (
    (ORG_HORSE_THERAPY_ID, "Umeå HK"),
    (ORG_STABLE_SERVICES_ID, "Anna Andersson"),
)


SEED_CURRENCY = "SEK"

# Business profile of the seeded organizations and of two customers (fake identifiers).
ORGANIZATION_PROFILES = {
    ORG_HORSE_THERAPY_ID: dict(
        legal_name="Fredrik Horse Therapy AB",
        address_line1="Storgatan 1",
        postal_code="903 26",
        city="Umeå",
        country_code="SE",
        registration_number="556000-0001",
        vat_number="SE556000000101",
    ),
    ORG_STABLE_SERVICES_ID: dict(
        legal_name="Umeå Stable Services AB",
        address_line1="Stallvägen 4",
        postal_code="905 80",
        city="Umeå",
        country_code="SE",
        registration_number="556000-0002",
        vat_number="SE556000000201",
    ),
}
CUSTOMER_PROFILES = {
    (ORG_HORSE_THERAPY_ID, "Umeå HK"): dict(
        address_line1="Ridvägen 2",
        postal_code="903 30",
        city="Umeå",
        country_code="SE",
        registration_number="802000-0001",
    ),
}


def _fill_missing(record, values: dict) -> None:
    """Set each attribute that is still empty; never overwrite what is there."""
    for name, value in values.items():
        if getattr(record, name) is None:
            setattr(record, name, value)


def _definition(db: Session, org_id: uuid.UUID, key: str, **fields) -> CustomFieldDefinition:
    definition = db.scalar(
        select(CustomFieldDefinition).where(
            CustomFieldDefinition.organization_id == org_id,
            CustomFieldDefinition.entity_type == "transaction_line",
            CustomFieldDefinition.key == key,
        )
    )
    if definition is None:
        definition = CustomFieldDefinition(
            organization_id=org_id, entity_type="transaction_line", key=key, field_type="reference", **fields
        )
        db.add(definition)
        db.flush()
    return definition


def _set_reference(db: Session, org_id: uuid.UUID, definition: CustomFieldDefinition, entity_id, target_id) -> None:
    exists = db.scalar(
        select(CustomFieldValue.id).where(
            CustomFieldValue.definition_id == definition.id, CustomFieldValue.entity_id == entity_id
        )
    )
    if exists is None:
        db.add(
            CustomFieldValue(
                organization_id=org_id,
                definition_id=definition.id,
                entity_type=definition.entity_type,
                field_type=definition.field_type,
                entity_id=entity_id,
                value_reference_id=target_id,
            )
        )


def _customer_id(db: Session, org_id: uuid.UUID, name: str) -> uuid.UUID:
    return db.scalar(
        select(Customer.id).where(Customer.organization_id == org_id, Customer.name == name)
    )


def seed(db: Session) -> None:
    """Create any missing seed rows. Does not commit."""
    for org_id, name in ORGANIZATIONS.items():
        if db.get(Organization, org_id) is None:
            db.add(Organization(id=org_id, name=name))
    db.flush()
    for org_id, profile in ORGANIZATION_PROFILES.items():
        organization = db.get(Organization, org_id)
        _fill_missing(organization, {"default_currency": SEED_CURRENCY, **profile})
    db.flush()

    for seed_user in USERS:
        user = db.scalar(select(User).where(User.email == seed_user.email))
        if user is None:
            user = User(email=seed_user.email, name=seed_user.name, can_create_organizations=seed_user.can_create_organizations)
            db.add(user)
            db.flush()
        for org_id, role in seed_user.memberships:
            exists = db.scalar(
                select(OrganizationUser.id).where(
                    OrganizationUser.organization_id == org_id,
                    OrganizationUser.user_id == user.id,
                )
            )
            if exists is None:
                db.add(OrganizationUser(organization_id=org_id, user_id=user.id, role=role))
    db.flush()

    for org_id, name, customer_type, email in CUSTOMERS:
        exists = db.scalar(
            select(Customer.id).where(Customer.organization_id == org_id, Customer.name == name)
        )
        if exists is None:
            db.add(
                Customer(organization_id=org_id, name=name, customer_type=customer_type, email=email)
            )
    db.flush()
    for (org_id, name), profile in CUSTOMER_PROFILES.items():
        customer = db.scalar(
            select(Customer).where(Customer.organization_id == org_id, Customer.name == name)
        )
        _fill_missing(customer, profile)
    db.flush()

    for org_id, name, item_type, unit, price, vat in ITEMS:
        exists = db.scalar(
            select(Item.id).where(Item.organization_id == org_id, Item.name == name)
        )
        if exists is None:
            db.add(
                Item(
                    organization_id=org_id,
                    name=name,
                    type=item_type,
                    unit=unit,
                    price_ex_vat=Decimal(price),
                    vat_rate=Decimal(vat),
                )
            )
    db.flush()

    for org_id, name, owner, stable, birth_year, sex, breed in HORSES:
        exists = db.scalar(
            select(Horse.id).where(Horse.organization_id == org_id, Horse.name == name)
        )
        if exists is None:
            db.add(
                Horse(
                    organization_id=org_id,
                    name=name,
                    owner_customer_id=_customer_id(db, org_id, owner),
                    stable_customer_id=_customer_id(db, org_id, stable) if stable else None,
                    birth_year=birth_year,
                    sex=sex,
                    breed=breed,
                )
            )
    db.flush()

    for org_id, billing_name in TRANSACTIONS:
        billing_id = _customer_id(db, org_id, billing_name)
        existing = db.scalar(
            select(Transaction).where(
                Transaction.organization_id == org_id,
                Transaction.billing_customer_id == billing_id,
                Transaction.transaction_date == SEED_TRANSACTION_DATE,
            )
        )
        if existing is not None:
            # A seeded transaction from before currencies existed gets the seed's explicit currency.
            _fill_missing(existing, {"currency": SEED_CURRENCY})
            continue
        item = db.scalar(
            select(Item).where(Item.organization_id == org_id, Item.name == "Horse massage")
        )
        transaction = Transaction(
            organization_id=org_id,
            billing_customer_id=billing_id,
            transaction_date=SEED_TRANSACTION_DATE,
            status=TransactionStatus.COMPLETED,
            currency=SEED_CURRENCY,
        )
        db.add(transaction)
        db.flush()
        quantity = Decimal("1")
        amounts = calculate_line(quantity, item.price_ex_vat, item.vat_rate)
        db.add(
            TransactionLine(
                organization_id=org_id,
                transaction_id=transaction.id,
                item_id=item.id,
                position=1,
                description=item.name,
                unit=item.unit,
                quantity=quantity,
                unit_price_ex_vat=item.price_ex_vat,
                vat_rate=item.vat_rate,
                net_amount=amounts.net,
                vat_amount=amounts.vat,
                gross_amount=amounts.gross,
            )
        )
    db.flush()

    # Custom fields on transaction lines: Owner (customer) -> Horse (filtered by that owner).
    for org_id in (ORG_HORSE_THERAPY_ID, ORG_STABLE_SERVICES_ID):
        owner = _definition(
            db, org_id, "owner", label="Owner", position=10, reference_source="customer"
        )
        horse = _definition(
            db,
            org_id,
            "horse",
            label="Horse",
            position=20,
            reference_source="horse",
            depends_on_definition_id=owner.id,
            depends_on_filter="owner_customer_id",
        )
        line = db.scalar(
            select(TransactionLine)
            .join(Transaction, Transaction.id == TransactionLine.transaction_id)
            .where(
                Transaction.organization_id == org_id,
                Transaction.transaction_date == SEED_TRANSACTION_DATE,
            )
        )
        kalle = db.scalar(select(Horse.id).where(Horse.organization_id == org_id, Horse.name == "Kalle"))
        _set_reference(db, org_id, owner, line.id, _customer_id(db, org_id, "Anna Andersson"))
        _set_reference(db, org_id, horse, line.id, kalle)
    db.flush()


def main() -> None:
    if settings.app_env != "development":
        raise SystemExit("Refusing to seed: APP_ENV is not 'development'.")
    with SessionLocal() as db:
        seed(db)
        db.commit()
    print("Seeded development data.")


if __name__ == "__main__":
    main()
