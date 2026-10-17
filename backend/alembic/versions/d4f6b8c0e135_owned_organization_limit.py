"""owned-organization limit replaces the organization-creation flag

Revision ID: d4f6b8c0e135
Revises: c3e5a7b9d024
Create Date: 2026-10-08 05:00:00.000000

`users.max_owned_organizations` (default 1: "Owned 1 / 1") counts owner-role memberships; membership never counts.
Existing accounts get max(1, what they own today), so nobody is over the limit after the migration. The old yes/no
`can_create_organizations` is dropped: creating is now allowed while the account owns fewer than its limit.

A trigger on organization_users refuses any NEW owner membership beyond the limit (insert as owner, or a role change
to owner), after locking the user's row so two grants to the same person serialize. The operator's `repair owner`
is the one override (transaction-local `app.owner_limit_override = on`).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "d4f6b8c0e135"
down_revision: Union[str, Sequence[str], None] = "c3e5a7b9d024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("max_owned_organizations", sa.Integer(), server_default=sa.text("1"), nullable=False))
    op.create_check_constraint("ck_users_max_owned_organizations", "users", "max_owned_organizations >= 0")
    op.execute(
        """
        UPDATE users u SET max_owned_organizations = GREATEST(1, (
            SELECT count(*) FROM organization_users ou WHERE ou.user_id = u.id AND ou.role = 'owner'
        ))
        """
    )
    op.drop_column("users", "can_create_organizations")
    op.execute(
        """
        CREATE FUNCTION organization_users_owner_limit() RETURNS trigger AS $$
        DECLARE
            allowed integer;
            owned integer;
        BEGIN
            IF TG_OP = 'UPDATE' AND OLD.role = 'owner' THEN
                RETURN NEW;  -- already an owner: nothing is gained
            END IF;
            IF current_setting('app.owner_limit_override', true) = 'on' THEN
                RETURN NEW;  -- the operator's repair, in its own transaction
            END IF;
            SELECT max_owned_organizations INTO allowed FROM users WHERE id = NEW.user_id FOR UPDATE;
            SELECT count(*) INTO owned FROM organization_users
                WHERE user_id = NEW.user_id AND role = 'owner' AND id <> NEW.id;
            IF owned >= allowed THEN
                RAISE EXCEPTION 'owned organization limit reached' USING ERRCODE = 'check_violation', CONSTRAINT = 'owned_organization_limit';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_organization_users_owner_limit
        BEFORE INSERT OR UPDATE OF role ON organization_users
        FOR EACH ROW WHEN (NEW.role = 'owner')
        EXECUTE FUNCTION organization_users_owner_limit()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_organization_users_owner_limit ON organization_users")
    op.execute("DROP FUNCTION organization_users_owner_limit()")
    op.add_column("users", sa.Column("can_create_organizations", sa.Boolean(), server_default=sa.text("false"), nullable=False))
    # The old flag meant "may create": true while the account owns fewer organizations than its limit.
    op.execute(
        """
        UPDATE users u SET can_create_organizations = (
            SELECT count(*) FROM organization_users ou WHERE ou.user_id = u.id AND ou.role = 'owner'
        ) < u.max_owned_organizations
        """
    )
    op.drop_constraint("ck_users_max_owned_organizations", "users", type_="check")
    op.drop_column("users", "max_owned_organizations")
