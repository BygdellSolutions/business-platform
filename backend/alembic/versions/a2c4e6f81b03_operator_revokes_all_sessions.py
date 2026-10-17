"""authentication: an operator can end every session at once

Additive: one new session revocation reason (`operator`) and one new security event type
(`sessions_revoked_by_operator`), so that a disaster or incident restore does not force anyone to keep the sessions
restored from the backup point, and the log says accurately what happened. Only the two CHECK lists change.

Revision ID: a2c4e6f81b03
Revises: e1a3c5e7b902
Create Date: 2026-10-15
"""

from typing import Sequence, Union

from alembic import op

revision: str = "a2c4e6f81b03"
down_revision: Union[str, Sequence[str], None] = "e1a3c5e7b902"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_REASONS = ("logout", "rotated", "password_changed", "cap", "disabled", "setup")
OLD_TYPES = (
    "login_success", "login_failure", "logout", "password_changed", "password_change_failure", "setup_link_issued",
    "setup_redeemed", "setup_failure", "user_disabled", "user_enabled", "organization_created", "capability_changed",
    "member_role_changed", "member_removed", "member_left", "owner_repaired", "invitation_created", "invitation_revoked",
    "invitation_accepted", "reauth_failure", "ownership_transferred", "organization_deleted",
)
NEW_REASONS = OLD_REASONS + ("operator",)
NEW_TYPES = OLD_TYPES + ("sessions_revoked_by_operator",)


def _in(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _replace(reasons: Sequence[str], types: Sequence[str]) -> None:
    op.drop_constraint("ck_auth_sessions_revoked_reason", "auth_sessions", type_="check")
    op.create_check_constraint("ck_auth_sessions_revoked_reason", "auth_sessions", f"revoked_reason IS NULL OR revoked_reason IN ({_in(reasons)})")
    op.drop_constraint("ck_security_events_type", "security_events", type_="check")
    op.create_check_constraint("ck_security_events_type", "security_events", f"event_type IN ({_in(types)})")


def upgrade() -> None:
    _replace(NEW_REASONS, NEW_TYPES)


def downgrade() -> None:
    # Sessions that ended with the new reason are relabelled so the narrower list can hold them. Security events are append-only
    # (recent ones cannot be deleted): the narrower event list is restored only when no event of the new kind exists.
    op.execute("UPDATE auth_sessions SET revoked_reason = 'disabled' WHERE revoked_reason = 'operator'")
    op.drop_constraint("ck_auth_sessions_revoked_reason", "auth_sessions", type_="check")
    op.create_check_constraint("ck_auth_sessions_revoked_reason", "auth_sessions", f"revoked_reason IS NULL OR revoked_reason IN ({_in(OLD_REASONS)})")
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM security_events WHERE event_type = 'sessions_revoked_by_operator') THEN
                ALTER TABLE security_events DROP CONSTRAINT ck_security_events_type;
                ALTER TABLE security_events ADD CONSTRAINT ck_security_events_type CHECK (event_type IN ({_in(OLD_TYPES)}));
            END IF;
        END $$
        """
    )
