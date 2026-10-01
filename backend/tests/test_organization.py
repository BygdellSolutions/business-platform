import uuid
from datetime import datetime

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from app.core.db import engine
from app.models import Organization


def test_organizations_table_is_migrated():
    columns = {c["name"]: c for c in inspect(engine).get_columns("organizations")}
    assert set(columns) == {"id", "name", "created_at", "updated_at"}
    assert not columns["name"]["nullable"]


def test_create_organization_generates_id_and_timestamps(db_session: Session):
    org = Organization(name="Fredrik Horse Therapy")
    db_session.add(org)
    db_session.flush()
    db_session.refresh(org)

    assert isinstance(org.id, uuid.UUID)
    assert isinstance(org.created_at, datetime)
    assert org.created_at.tzinfo is not None
    assert org.updated_at is not None


def test_organizations_are_distinct_records(db_session: Session):
    a = Organization(name="Org A")
    b = Organization(name="Org A")  # same name, different tenant: names are not unique
    db_session.add_all([a, b])
    db_session.flush()

    assert a.id != b.id
    names = db_session.scalars(
        select(Organization.id).where(Organization.name == "Org A")
    ).all()
    assert set(names) == {a.id, b.id}

