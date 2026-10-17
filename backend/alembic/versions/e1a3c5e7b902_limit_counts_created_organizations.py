"""the organization limit counts organizations CREATED, not owned

Revision ID: e1a3c5e7b902
Revises: b8d0f2a4c613
Create Date: 2026-10-12 21:00:00.000000

`users.max_owned_organizations` keeps its name but now means "how many organizations this account may create"
(app.core.ownership). Becoming the owner of an organization that already exists (an owner invitation, a promotion, a
transfer) is no longer limited, so the trigger that capped owner memberships is dropped. New accounts get 0: only
the operator (`bootstrap-user`, `set-owned-limit`) gives the right to create. Existing limits are left as they are.
Creations that wrote no creation record (no retry key) get one from their security event.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "e1a3c5e7b902"
down_revision: Union[str, Sequence[str], None] = "b8d0f2a4c613"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DROP TRIGGER trg_organization_users_owner_limit ON organization_users")
    op.execute("DROP FUNCTION organization_users_owner_limit()")
    op.alter_column("users", "max_owned_organizations", server_default=sa.text("0"))
    # The limit counts creation records. Creations made without a retry key wrote none until now: record them from
    # their `organization_created` security event (random key and hash, in the shapes the checks require).
    op.execute(
        """
        INSERT INTO organization_creation_requests (user_id, request_key, request_hash, organization_id, created_at)
        SELECT DISTINCT ON (e.organization_id) e.actor_user_id,
               md5(random()::text) || substr(md5(random()::text), 1, 11),
               md5(random()::text) || md5(random()::text),
               e.organization_id, e.occurred_at
        FROM security_events e
        JOIN organizations o ON o.id = e.organization_id
        JOIN users u ON u.id = e.actor_user_id
        WHERE e.event_type = 'organization_created'
          AND NOT EXISTS (SELECT 1 FROM organization_creation_requests r WHERE r.organization_id = e.organization_id)
        ORDER BY e.organization_id, e.occurred_at
        """
    )


def downgrade() -> None:
    op.alter_column("users", "max_owned_organizations", server_default=sa.text("1"))
    # The trigger of d4f6b8c0e135, as it was: it guards NEW owner memberships only, so existing rows are untouched.
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
