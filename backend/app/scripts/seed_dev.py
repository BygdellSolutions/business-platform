"""Seed development data. Safe to re-run. Refuses to run outside APP_ENV=development.

    python -m app.scripts.seed_dev

Layout (chosen to exercise tenant selection and negative membership cases):

    fredrik@dev.test  owner of Fredrik Horse Therapy, admin of Umeå Stable Services
    maria@dev.test    employee of Umeå Stable Services only

Both organizations have a customer named "Anna Andersson" (identical-looking
data in different tenants), which is what isolation checks should be run against.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.db import SessionLocal
from app.models import Customer, CustomerType, Organization, OrganizationUser, Role, User

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


def main() -> None:
    if settings.app_env != "development":
        raise SystemExit("Refusing to seed: APP_ENV is not 'development'.")
    with SessionLocal() as db:
        seed(db)
        db.commit()
    print("Seeded development data.")


if __name__ == "__main__":
    main()
