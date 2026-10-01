import uuid

from sqlalchemy.orm import Session

from app.models import Organization, OrganizationUser, Role, User


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
