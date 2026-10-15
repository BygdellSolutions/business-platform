"""authentication: a second factor (TOTP) with recovery codes

Additive. `user_mfa` holds a random, non-secret seed per person (the TOTP key is derived from it with a server-side key, so the
database alone cannot produce a code) and the time step last accepted; `user_recovery_codes` holds single-use codes as SHA-256
hashes; `mfa_challenges` is the short-lived step between a correct password and a session. Five new security event types.

Revision ID: c5e7a9b1d3f4
Revises: a2c4e6f81b03
Create Date: 2026-10-15
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c5e7a9b1d3f4"
down_revision: Union[str, Sequence[str], None] = "a2c4e6f81b03"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_TYPES = (
    "login_success", "login_failure", "logout", "password_changed", "password_change_failure", "setup_link_issued",
    "setup_redeemed", "setup_failure", "user_disabled", "user_enabled", "organization_created", "capability_changed",
    "member_role_changed", "member_removed", "member_left", "owner_repaired", "invitation_created", "invitation_revoked",
    "invitation_accepted", "reauth_failure", "ownership_transferred", "organization_deleted", "sessions_revoked_by_operator",
)
NEW_TYPES = OLD_TYPES + ("mfa_enabled", "mfa_disabled", "mfa_failure", "mfa_reset_by_operator", "recovery_code_used")
HEX64 = "^[0-9a-f]{64}$"


def _in(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _types(values: Sequence[str]) -> None:
    op.drop_constraint("ck_security_events_type", "security_events", type_="check")
    op.create_check_constraint("ck_security_events_type", "security_events", f"event_type IN ({_in(values)})")


def _narrow_types_if_unused() -> None:
    """Security events are append-only (recent ones cannot be deleted), so the narrower list is restored only when no event of the
    new kinds exists; otherwise the wider list stays and the old code simply never writes them."""
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM security_events WHERE event_type IN ({_in(NEW_TYPES[len(OLD_TYPES):])})) THEN
                ALTER TABLE security_events DROP CONSTRAINT ck_security_events_type;
                ALTER TABLE security_events ADD CONSTRAINT ck_security_events_type CHECK (event_type IN ({_in(OLD_TYPES)}));
            END IF;
        END $$
        """
    )


def upgrade() -> None:
    op.create_table(
        "user_mfa",
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("seed", sa.String(length=32), nullable=False),
        sa.Column("enabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_step", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("seed ~ '^[0-9a-f]{32}$'", name="ck_user_mfa_seed_shape"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_table(
        "user_recovery_codes",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(f"code_hash ~ '{HEX64}'", name="ck_user_recovery_codes_hash_shape"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code_hash"),
    )
    op.create_index("ix_user_recovery_codes_user", "user_recovery_codes", ["user_id"])
    op.create_table(
        "mfa_challenges",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=True),
        sa.CheckConstraint(f"token_hash ~ '{HEX64}'", name="ck_mfa_challenges_hash_shape"),
        sa.CheckConstraint("attempts >= 0", name="ck_mfa_challenges_attempts"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash"),
    )
    op.create_index("ix_mfa_challenges_expires_at", "mfa_challenges", ["expires_at"])
    _types(NEW_TYPES)


def downgrade() -> None:
    _narrow_types_if_unused()
    op.drop_index("ix_mfa_challenges_expires_at", table_name="mfa_challenges")
    op.drop_table("mfa_challenges")
    op.drop_index("ix_user_recovery_codes_user", table_name="user_recovery_codes")
    op.drop_table("user_recovery_codes")
    op.drop_table("user_mfa")
