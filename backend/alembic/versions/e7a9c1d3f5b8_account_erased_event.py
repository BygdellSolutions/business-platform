"""authentication: one security event type for an account the operator erased

Additive: one more value in the security event type list. Security events are append-only, so the downgrade narrows the list again
only when no event of the new kind exists.

Revision ID: e7a9c1d3f5b8
Revises: d6f8b0c2e4a7
Create Date: 2026-10-15
"""

from typing import Sequence, Union

from alembic import op

revision: str = "e7a9c1d3f5b8"
down_revision: Union[str, Sequence[str], None] = "d6f8b0c2e4a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_TYPES = (
    "login_success", "login_failure", "logout", "password_changed", "password_change_failure", "setup_link_issued",
    "setup_redeemed", "setup_failure", "user_disabled", "user_enabled", "organization_created", "capability_changed",
    "member_role_changed", "member_removed", "member_left", "owner_repaired", "invitation_created", "invitation_revoked",
    "invitation_accepted", "reauth_failure", "ownership_transferred", "organization_deleted", "sessions_revoked_by_operator",
    "mfa_enabled", "mfa_disabled", "mfa_failure", "mfa_reset_by_operator", "recovery_code_used",
)
NEW_TYPES = OLD_TYPES + ("account_erased",)


def _in(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.drop_constraint("ck_security_events_type", "security_events", type_="check")
    op.create_check_constraint("ck_security_events_type", "security_events", f"event_type IN ({_in(NEW_TYPES)})")


def downgrade() -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM security_events WHERE event_type = 'account_erased') THEN
                ALTER TABLE security_events DROP CONSTRAINT ck_security_events_type;
                ALTER TABLE security_events ADD CONSTRAINT ck_security_events_type CHECK (event_type IN ({_in(OLD_TYPES)}));
            END IF;
        END $$
        """
    )
