import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models import OrganizationUser, Role, User
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


def test_seed_refuses_outside_development(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "app_env", "production")
    with pytest.raises(SystemExit):
        seed_dev.main()
