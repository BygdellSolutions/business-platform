"""organization deletion escape for protected rows, and the security events of the danger zone

Revision ID: c3e5a7b9d024
Revises: b2d4f6a8c013
Create Date: 2026-10-08 03:00:00.000000

An organization can be deleted for good by an owner (with strong confirmation). Issued invoices, their documents
and frozen PDFs refuse every DELETE; they now let one through only while the deleting transaction has set
`app.deleting_organization` to THAT row's organization (a transaction-local setting, so it never leaks into another
request and never opens another organization's rows). Nothing else about those protections changes.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "c3e5a7b9d024"
down_revision: Union[str, Sequence[str], None] = "b2d4f6a8c013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OLD_TYPES = (
    "'login_success', 'login_failure', 'logout', 'password_changed', 'password_change_failure', 'setup_link_issued', "
    "'setup_redeemed', 'setup_failure', 'user_disabled', 'user_enabled', 'organization_created', 'capability_changed', "
    "'member_role_changed', 'member_removed', 'member_left', 'owner_repaired', 'invitation_created', "
    "'invitation_revoked', 'invitation_accepted'"
)
NEW_TYPES = OLD_TYPES + ", 'reauth_failure', 'ownership_transferred', 'organization_deleted'"

DELETING = "current_setting('app.deleting_organization', true) = OLD.organization_id::text"


def _invoices(escape: bool) -> str:
    allow = f"IF {DELETING} THEN RETURN OLD; END IF;" if escape else ""
    return f"""
        CREATE OR REPLACE FUNCTION invoices_immutability() RETURNS trigger AS $$
        BEGIN
            IF TG_OP = 'DELETE' THEN
                {allow}
                IF OLD.status = 'issued' THEN
                    RAISE EXCEPTION 'an issued invoice cannot be deleted' USING ERRCODE = 'check_violation';
                END IF;
                RETURN OLD;
            END IF;
            IF OLD.status = 'issued' THEN
                RAISE EXCEPTION 'an issued invoice cannot be changed' USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.id <> OLD.id OR NEW.organization_id <> OLD.organization_id
               OR NEW.customer_id <> OLD.customer_id OR NEW.currency <> OLD.currency
               OR NEW.series <> OLD.series
               OR NEW.net_amount <> OLD.net_amount OR NEW.vat_amount <> OLD.vat_amount
               OR NEW.gross_amount <> OLD.gross_amount THEN
                RAISE EXCEPTION 'the customer, currency and totals of an invoice cannot be changed'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """


def _children(escape: bool) -> str:
    allow = f"IF TG_OP = 'DELETE' AND {DELETING} THEN RETURN OLD; END IF;" if escape else ""
    return f"""
        CREATE OR REPLACE FUNCTION invoice_children_immutability() RETURNS trigger AS $$
        DECLARE
            invoice uuid;
            org uuid;
            current_status text;
            mutable text := COALESCE(TG_ARGV[0], '');
        BEGIN
            {allow}
            IF TG_OP = 'DELETE' THEN
                invoice := OLD.invoice_id; org := OLD.organization_id;
            ELSE
                invoice := NEW.invoice_id; org := NEW.organization_id;
            END IF;
            -- During the deletion of a draft invoice the parent row is already gone: nothing to protect.
            SELECT status INTO current_status FROM invoices WHERE organization_id = org AND id = invoice;
            IF current_status = 'issued' THEN
                RAISE EXCEPTION 'the documents of an issued invoice cannot be changed'
                    USING ERRCODE = 'check_violation';
            END IF;
            IF TG_OP = 'UPDATE' THEN
                IF NEW.invoice_id <> OLD.invoice_id THEN
                    RAISE EXCEPTION 'a document row cannot move to another invoice' USING ERRCODE = 'check_violation';
                END IF;
                -- While a draft, only the snapshot content named by the trigger argument may change.
                IF (to_jsonb(NEW) - mutable - 'updated_at') IS DISTINCT FROM (to_jsonb(OLD) - mutable - 'updated_at') THEN
                    RAISE EXCEPTION 'only snapshot content of a draft invoice may be changed'
                        USING ERRCODE = 'check_violation';
                END IF;
            END IF;
            IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
    """


def _pdfs(escape: bool) -> str:
    allow = f"IF TG_OP = 'DELETE' AND {DELETING} THEN RETURN OLD; END IF;" if escape else ""
    return f"""
        CREATE OR REPLACE FUNCTION invoice_pdfs_guard() RETURNS trigger AS $$
        DECLARE
            current_status text;
        BEGIN
            IF TG_OP = 'INSERT' THEN
                SELECT status INTO current_status FROM invoices
                    WHERE organization_id = NEW.organization_id AND id = NEW.invoice_id;
                IF current_status IS DISTINCT FROM 'issued' THEN
                    RAISE EXCEPTION 'only an issued invoice can have a PDF' USING ERRCODE = 'check_violation';
                END IF;
                RETURN NEW;
            END IF;
            {allow}
            RAISE EXCEPTION 'a frozen invoice PDF cannot be changed or deleted' USING ERRCODE = 'check_violation';
        END;
        $$ LANGUAGE plpgsql
    """


def _event_types(types: str) -> None:
    op.execute("ALTER TABLE security_events DROP CONSTRAINT ck_security_events_type")
    op.execute(f"ALTER TABLE security_events ADD CONSTRAINT ck_security_events_type CHECK (event_type IN ({types}))")


def upgrade() -> None:
    _event_types(NEW_TYPES)
    for build in (_invoices, _children, _pdfs):
        op.execute(build(escape=True))


def downgrade() -> None:
    for build in (_invoices, _children, _pdfs):
        op.execute(build(escape=False))
    # Security events are append-only (recent ones cannot be deleted), so the narrower list is restored only when
    # no event of the new kinds exists; otherwise the wider list stays and the old code simply never writes them.
    op.execute(
        f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM security_events
                           WHERE event_type IN ('reauth_failure', 'ownership_transferred', 'organization_deleted')) THEN
                ALTER TABLE security_events DROP CONSTRAINT ck_security_events_type;
                ALTER TABLE security_events ADD CONSTRAINT ck_security_events_type CHECK (event_type IN ({OLD_TYPES}));
            END IF;
        END $$
        """
    )
