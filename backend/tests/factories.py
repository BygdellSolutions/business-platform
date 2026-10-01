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
