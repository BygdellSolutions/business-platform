import uuid
from datetime import date
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
from app.modules.sales.models import Transaction, TransactionLine
from app.modules.sales.pricing import calculate_line


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


def make_transaction(
    db: Session,
    org: Organization,
    billing_customer: Customer | None = None,
    transaction_date: date = date(2026, 10, 1),
    status: str = "draft",
    lines: list[dict] | None = None,
) -> Transaction:
    """A transaction whose defaults look identical in every organization.

    `lines` is a list of make_line() keyword dicts; by default one ad-hoc line.
    """
    billing_customer = billing_customer or make_customer(
        db, org, "Umeå HK", CustomerType.COMPANY, "hk@example.test", None
    )
    tx = Transaction(
        organization_id=org.id,
        billing_customer_id=billing_customer.id,
        transaction_date=transaction_date,
        status=status,
    )
    db.add(tx)
    db.flush()
    for position, fields in enumerate(lines if lines is not None else [{}], start=1):
        make_line(db, org, tx, position=position, **fields)
    return tx


def make_line(
    db: Session,
    org: Organization,
    transaction: Transaction,
    item: Item | None = None,
    description: str = "Horse massage",
    unit: str = "session",
    quantity: Decimal | str = "1",
    unit_price_ex_vat: Decimal | str = "850.00",
    vat_rate: Decimal | str = "25.00",
    position: int = 1,
    **fields,
) -> TransactionLine:
    """A line with consistent stored amounts (override net_amount etc. to build bad rows)."""
    quantity, unit_price_ex_vat, vat_rate = (
        Decimal(quantity),
        Decimal(unit_price_ex_vat),
        Decimal(vat_rate),
    )
    amounts = calculate_line(quantity, unit_price_ex_vat, vat_rate)
    fields.setdefault("net_amount", amounts.net)
    fields.setdefault("vat_amount", amounts.vat)
    fields.setdefault("gross_amount", amounts.gross)
    line = TransactionLine(
        organization_id=org.id,
        transaction_id=transaction.id,
        item_id=item.id if item is not None else None,
        position=position,
        description=description,
        unit=unit,
        quantity=quantity,
        unit_price_ex_vat=unit_price_ex_vat,
        vat_rate=vat_rate,
        **fields,
    )
    db.add(line)
    db.flush()
    return line
