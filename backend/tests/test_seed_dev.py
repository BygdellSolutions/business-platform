from decimal import Decimal

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import Customer, Item, Organization, OrganizationUser, Role, User
from app.modules.custom_fields.models import CustomFieldDefinition, CustomFieldValue
from app.modules.equine.models import Horse
from app.modules.sales.models import Transaction, TransactionLine
from app.scripts import seed_dev
from tests.factories import make_org, make_transaction


def memberships(db: Session, email: str) -> dict:
    rows = db.execute(
        select(OrganizationUser.organization_id, OrganizationUser.role)
        .join(User, User.id == OrganizationUser.user_id)
        .where(User.email == email)
    )
    return {org_id: role for org_id, role in rows}


def test_seed_creates_expected_layout_and_is_idempotent(db_session: Session):
    seed_dev.seed(db_session)
    seed_dev.seed(db_session)

    assert memberships(db_session, "fredrik@dev.test") == {
        seed_dev.ORG_HORSE_THERAPY_ID: Role.OWNER,
        seed_dev.ORG_STABLE_SERVICES_ID: Role.ADMIN,
    }
    assert memberships(db_session, "maria@dev.test") == {
        seed_dev.ORG_STABLE_SERVICES_ID: Role.EMPLOYEE,
    }
    seeded_users = db_session.scalar(
        select(func.count()).select_from(User).where(User.email.like("%@dev.test"))
    )
    assert seeded_users == 2

    names_by_org = {
        org_id: sorted(
            db_session.scalars(select(Customer.name).where(Customer.organization_id == org_id))
        )
        for org_id in (seed_dev.ORG_HORSE_THERAPY_ID, seed_dev.ORG_STABLE_SERVICES_ID)
    }
    assert names_by_org[seed_dev.ORG_HORSE_THERAPY_ID] == ["Anna Andersson", "Umeå HK"]
    assert names_by_org[seed_dev.ORG_STABLE_SERVICES_ID] == ["Anna Andersson"]

    items = db_session.scalars(
        select(Item).where(
            Item.organization_id.in_(
                [seed_dev.ORG_HORSE_THERAPY_ID, seed_dev.ORG_STABLE_SERVICES_ID]
            )
        )
    ).all()
    assert sorted(i.organization_id for i in items) == sorted(
        [seed_dev.ORG_HORSE_THERAPY_ID, seed_dev.ORG_STABLE_SERVICES_ID]
    )
    assert {(i.name, i.type, i.unit, i.price_ex_vat, i.vat_rate) for i in items} == {
        ("Horse massage", "service", "session", Decimal("850.00"), Decimal("25.00"))
    }

    horses = {
        h.organization_id: h
        for h in db_session.scalars(
            select(Horse).where(
                Horse.organization_id.in_(
                    [seed_dev.ORG_HORSE_THERAPY_ID, seed_dev.ORG_STABLE_SERVICES_ID]
                )
            )
        )
    }
    assert {h.name for h in horses.values()} == {"Kalle"}
    names = {c.id: c.name for c in db_session.scalars(select(Customer))}
    therapy = horses[seed_dev.ORG_HORSE_THERAPY_ID]
    assert names[therapy.owner_customer_id] == "Anna Andersson"
    assert names[therapy.stable_customer_id] == "Umeå HK"
    assert (therapy.birth_year, therapy.sex, therapy.breed) == (2015, "gelding", "Swedish Warmblood")
    assert horses[seed_dev.ORG_STABLE_SERVICES_ID].stable_customer_id is None

    for org_id, billing in (
        (seed_dev.ORG_HORSE_THERAPY_ID, "Umeå HK"),
        (seed_dev.ORG_STABLE_SERVICES_ID, "Anna Andersson"),
    ):
        [tx] = db_session.scalars(select(Transaction).where(Transaction.organization_id == org_id)).all()
        assert names[tx.billing_customer_id] == billing
        assert (tx.status, tx.transaction_date) == ("completed", seed_dev.SEED_TRANSACTION_DATE)
        [line] = db_session.scalars(select(TransactionLine).where(TransactionLine.transaction_id == tx.id)).all()
        assert (line.description, line.unit, line.quantity) == ("Horse massage", "session", Decimal("1"))
        assert (line.net_amount, line.vat_amount, line.gross_amount) == (
            Decimal("850.00"), Decimal("212.50"), Decimal("1062.50"))

    for org_id in (seed_dev.ORG_HORSE_THERAPY_ID, seed_dev.ORG_STABLE_SERVICES_ID):
        definitions = {
            d.key: d
            for d in db_session.scalars(
                select(CustomFieldDefinition).where(CustomFieldDefinition.organization_id == org_id)
            )
        }
        assert set(definitions) == {"owner", "horse"}  # the same keys in both organizations
        assert definitions["horse"].depends_on_definition_id == definitions["owner"].id
        assert definitions["horse"].depends_on_filter == "owner_customer_id"
        shown = {
            d.key: db_session.scalar(
                select(CustomFieldValue.value_reference_id).where(CustomFieldValue.definition_id == d.id)
            )
            for d in definitions.values()
        }
        assert names[shown["owner"]] == "Anna Andersson"
        horse = db_session.get(Horse, shown["horse"])
        assert horse.name == "Kalle" and horse.organization_id == org_id and horse.owner_customer_id == shown["owner"]


def test_seed_refuses_outside_development(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "app_env", "production")
    with pytest.raises(SystemExit):
        seed_dev.main()


def test_seed_explicitly_configures_sek_and_a_profile_for_its_own_organizations(db_session: Session):
    seed_dev.seed(db_session)

    for org_id in (seed_dev.ORG_HORSE_THERAPY_ID, seed_dev.ORG_STABLE_SERVICES_ID):
        org = db_session.get(Organization, org_id)
        assert org.default_currency == "SEK"
        assert org.legal_name and org.vat_number and org.country_code == "SE"
        [tx] = db_session.scalars(select(Transaction).where(Transaction.organization_id == org_id)).all()
        assert tx.currency == "SEK"
    hk = db_session.scalar(select(Customer).where(Customer.name == "Umeå HK"))
    assert (hk.city, hk.country_code, hk.registration_number) == ("Umeå", "SE", "802000-0001")


def test_reseeding_never_overwrites_what_a_person_edited(db_session: Session):
    seed_dev.seed(db_session)
    org = db_session.get(Organization, seed_dev.ORG_HORSE_THERAPY_ID)
    org.legal_name, org.city = "Edited Name", None  # one value changed, one cleared by hand
    db_session.flush()

    seed_dev.seed(db_session)

    db_session.refresh(org)
    assert org.legal_name == "Edited Name"  # kept
    assert org.city == "Umeå"  # an EMPTY field is filled in again; that is the only thing the seed does


def test_seeding_an_older_database_gives_only_the_seeded_rows_a_currency(db_session: Session):
    """A development database seeded before currencies existed has seeded rows without one. The
    seed (data that owns those rows) assigns SEK to them, and to nothing else."""
    seed_dev.seed(db_session)
    db_session.execute(text("update organizations set default_currency = null where id = :o"), {"o": seed_dev.ORG_HORSE_THERAPY_ID})
    # The immutability trigger (rightly) forbids NULLing a currency; switch it off just for this
    # test's rolled-back transaction to recreate a seeded transaction from before currencies.
    db_session.execute(text("alter table transactions disable trigger trg_transactions_currency_immutable"))
    db_session.execute(text("update transactions set currency = null where organization_id = :o"), {"o": seed_dev.ORG_HORSE_THERAPY_ID})
    db_session.execute(text("alter table transactions enable trigger trg_transactions_currency_immutable"))
    stranger = make_org(db_session, "Not seeded", default_currency=None)
    foreign_tx = make_transaction(db_session, stranger, currency=None)

    seed_dev.seed(db_session)

    assert db_session.get(Organization, seed_dev.ORG_HORSE_THERAPY_ID).default_currency == "SEK"
    assert db_session.scalar(select(Transaction.currency).where(Transaction.organization_id == seed_dev.ORG_HORSE_THERAPY_ID)) == "SEK"
    db_session.refresh(stranger), db_session.refresh(foreign_tx)
    assert stranger.default_currency is None and foreign_tx.currency is None
