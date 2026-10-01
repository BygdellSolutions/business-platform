from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import Customer, Item, OrganizationUser, Role, User
from app.scripts import seed_dev


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


def test_seed_refuses_outside_development(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "app_env", "production")
    with pytest.raises(SystemExit):
        seed_dev.main()
