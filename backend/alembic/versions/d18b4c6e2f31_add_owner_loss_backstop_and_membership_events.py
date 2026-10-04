"""add the owner-loss backstop on organization_users and the membership security events

Revision ID: d18b4c6e2f31
Revises: c07a3e5f9b24
Create Date: 2026-10-04 21:00:00

An organization that has an owner must not LOSE its last owner through the loss of an owner row: a DELETE of an owner,
or an UPDATE that demotes an owner or moves the row to another organization. The rule is a deferred constraint trigger
(it runs at COMMIT, so a transaction may demote one owner and promote another) that counts the organization's owners
under a per-organization advisory lock (so two transactions that each remove a DIFFERENT owner cannot both pass).

It fires only for a row that WAS an owner, so a legacy organization that already has no owner is not invalidated and
its unrelated membership changes are unaffected; it does not guarantee the initial owner of a new organization (the
creation transaction does) and it repairs nothing. The migration adds no membership, promotes nobody, changes no role.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "d18b4c6e2f31"
down_revision: Union[str, Sequence[str], None] = "c07a3e5f9b24"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_TYPES = "'login_success', 'login_failure', 'logout', 'password_changed', 'password_change_failure', 'setup_link_issued', 'setup_redeemed', 'setup_failure', 'user_disabled', 'user_enabled', 'organization_created', 'capability_changed'"
NEW_TYPES = OLD_TYPES + ", 'member_role_changed', 'member_removed', 'member_left', 'owner_repaired'"


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION organization_users_owner_required() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            -- Serialize the check per organization: two transactions that each remove a different owner of a
            -- two-owner organization cannot both count the other's still-visible owner row.
            PERFORM pg_advisory_xact_lock(hashtextextended('organization-owners:' || OLD.organization_id::text, 0));
            IF EXISTS (SELECT 1 FROM organizations WHERE id = OLD.organization_id)
               AND NOT EXISTS (SELECT 1 FROM organization_users WHERE organization_id = OLD.organization_id AND role = 'owner') THEN
                RAISE EXCEPTION 'organization % would be left without an owner', OLD.organization_id
                    USING ERRCODE = 'check_violation', CONSTRAINT = 'organization_owner_required';
            END IF;
            RETURN NULL;
        END
        $$
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_organization_users_owner_required_delete
            AFTER DELETE ON organization_users
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW WHEN (OLD.role = 'owner')
            EXECUTE FUNCTION organization_users_owner_required()
        """
    )
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_organization_users_owner_required_update
            AFTER UPDATE OF role, organization_id ON organization_users
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW WHEN (OLD.role = 'owner' AND (NEW.role <> 'owner' OR NEW.organization_id <> OLD.organization_id))
            EXECUTE FUNCTION organization_users_owner_required()
        """
    )
    op.drop_constraint("ck_security_events_type", "security_events", type_="check")
    op.create_check_constraint("ck_security_events_type", "security_events", f"event_type IN ({NEW_TYPES})")


def downgrade() -> None:
    # The events of the new kinds are removed first (the append-only trigger is lifted for this one statement).
    op.execute("ALTER TABLE security_events DISABLE TRIGGER trg_security_events_append_only")
    op.execute("DELETE FROM security_events WHERE event_type IN ('member_role_changed', 'member_removed', 'member_left', 'owner_repaired')")
    op.execute("ALTER TABLE security_events ENABLE TRIGGER trg_security_events_append_only")
    op.drop_constraint("ck_security_events_type", "security_events", type_="check")
    op.create_check_constraint("ck_security_events_type", "security_events", f"event_type IN ({OLD_TYPES})")
    op.execute("DROP TRIGGER trg_organization_users_owner_required_update ON organization_users")
    op.execute("DROP TRIGGER trg_organization_users_owner_required_delete ON organization_users")
    op.execute("DROP FUNCTION organization_users_owner_required()")
