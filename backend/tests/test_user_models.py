import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import OrganizationUser, Role, User
from tests.factories import add_member, make_org, make_user


def test_user_defaults(db_session: Session):
    user = make_user(db_session)
    db_session.refresh(user)
    assert user.is_active is True
    assert user.created_at is not None


def test_email_must_be_lowercase(db_session: Session):
    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.add(User(email="Mixed@Tests.invalid", name="x"))
        db_session.flush()


def test_email_is_unique(db_session: Session):
    make_user(db_session, email="dup@tests.invalid")
    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.add(User(email="dup@tests.invalid", name="x"))
        db_session.flush()


def test_user_can_belong_to_several_organizations(db_session: Session):
    user = make_user(db_session)
    add_member(db_session, make_org(db_session, "A"), user, Role.OWNER)
    add_member(db_session, make_org(db_session, "B"), user, Role.VIEWER)
    count = db_session.scalar(
        select(func.count()).select_from(OrganizationUser).where(OrganizationUser.user_id == user.id)
    )
    assert count == 2


def test_membership_is_unique_per_user_and_organization(db_session: Session):
    org, user = make_org(db_session), make_user(db_session)
    add_member(db_session, org, user)
    with pytest.raises(IntegrityError), db_session.begin_nested():
        add_member(db_session, org, user, Role.ADMIN)


def test_role_must_be_a_known_role(db_session: Session):
    org, user = make_org(db_session), make_user(db_session)
    with pytest.raises(IntegrityError), db_session.begin_nested():
        db_session.add(OrganizationUser(organization_id=org.id, user_id=user.id, role="superuser"))
        db_session.flush()
