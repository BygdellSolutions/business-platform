import uuid
from decimal import Decimal

from sqlalchemy.orm import Session

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


def make_org(db: Session, name: str = "Test Org") -> Organization:
    org = Organization(name=name)
    db.add(org)
    db.flush()
    return org


def make_user(db: Session, email: str | None = None, **fields) -> User:
    # .invalid never collides with seeded @dev.test users.
    email = email or f"{uuid.uuid4().hex[:8]}@tests.invalid"
    user = User(email=email, name=fields.pop("name", "Test User"), **fields)
    db.add(user)
    db.flush()
    return user


def add_member(
    db: Session, org: Organization, user: User, role: Role = Role.EMPLOYEE
) -> OrganizationUser:
    membership = OrganizationUser(organization_id=org.id, user_id=user.id, role=role)
    db.add(membership)
    db.flush()
    return membership


def make_customer(
    db: Session,
    org: Organization,
    name: str = "Anna Andersson",
    customer_type: CustomerType = CustomerType.PERSON,
    email: str | None = "anna@example.test",
    phone: str | None = "070-000 00 00",
    **fields,
) -> Customer:
    customer = Customer(
        organization_id=org.id,
        name=name,
        customer_type=customer_type,
        email=email,
        phone=phone,
        **fields,
    )
    db.add(customer)
    db.flush()
    return customer


def make_item(
    db: Session,
    org: Organization,
    name: str = "Horse massage",
    type: ItemType = ItemType.SERVICE,
    unit: str = "session",
    price_ex_vat: Decimal | str = "850.00",
    vat_rate: Decimal | str = "25.00",
    **fields,
) -> Item:
    item = Item(
        organization_id=org.id,
        name=name,
        type=type,
        unit=unit,
        price_ex_vat=Decimal(price_ex_vat),
        vat_rate=Decimal(vat_rate),
        **fields,
    )
    db.add(item)
    db.flush()
    return item


def make_horse(
    db: Session,
    org: Organization,
    name: str = "Kalle",
    owner: Customer | None = None,
    stable: Customer | None = None,
    **fields,
) -> Horse:
    """A horse whose defaults look identical in every organization (including its owner)."""
    owner = owner or make_customer(db, org)  # "Anna Andersson", like the seed
    fields.setdefault("birth_year", 2015)
    fields.setdefault("sex", "gelding")
    fields.setdefault("breed", "Swedish Warmblood")
    horse = Horse(
        organization_id=org.id,
        name=name,
        owner_customer_id=owner.id,
        stable_customer_id=stable.id if stable is not None else None,
        **fields,
    )
    db.add(horse)
    db.flush()
    return horse
