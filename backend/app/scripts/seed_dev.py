"""Seed development data. Safe to re-run. Refuses to run outside APP_ENV=development.

    python -m app.scripts.seed_dev

Layout (chosen to exercise tenant selection and negative membership cases):

    fredrik@dev.test  owner of Fredrik Horse Therapy, admin of Umeå Stable Services
    maria@dev.test    employee of Umeå Stable Services only

Both organizations have a customer named "Anna Andersson", an item named
"Horse massage" and a horse named "Kalle" (identical-looking data in different
tenants), which is what isolation checks should be run against.
"""

import uuid
from dataclasses import dataclass
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
from app.modules.equine.models import Horse

# Fixed ids make the seed idempotent and let docs/tests refer to known tenants.
ORG_HORSE_THERAPY_ID = uuid.UUID("00000000-0000-4000-8000-0000000000a1")
ORG_STABLE_SERVICES_ID = uuid.UUID("00000000-0000-4000-8000-0000000000b2")


@dataclass(frozen=True)
class SeedUser:
    email: str
    name: str
    memberships: tuple[tuple[uuid.UUID, Role], ...]


ORGANIZATIONS = {
    ORG_HORSE_THERAPY_ID: "Fredrik Horse Therapy",
    ORG_STABLE_SERVICES_ID: "Umeå Stable Services",
}

USERS = (
    SeedUser(
        "fredrik@dev.test",
        "Fredrik (dev)",
        ((ORG_HORSE_THERAPY_ID, Role.OWNER), (ORG_STABLE_SERVICES_ID, Role.ADMIN)),
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

    for seed_user in USERS:
        user = db.scalar(select(User).where(User.email == seed_user.email))
        if user is None:
            user = User(email=seed_user.email, name=seed_user.name)
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


def main() -> None:
    if settings.app_env != "development":
        raise SystemExit("Refusing to seed: APP_ENV is not 'development'.")
    with SessionLocal() as db:
        seed(db)
        db.commit()
    print("Seeded development data.")


if __name__ == "__main__":
    main()
