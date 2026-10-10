"""A disposable, representative database for the backup/restore tests and drill.

    DATABASE_URL=<a freshly MIGRATED disposable database> python -m tests.restore_fixture

Run it as its own process (the application binds its engine to DATABASE_URL when imported). It fills the database through the
application (API and models), never through hand-written SQL on business tables, so every row is one the real code can produce and
nothing is in an invalid state. It prints one JSON object of identifiers the tests need (ids, a live session token for the backend
smoke, SENTINEL strings): the database is disposable, and no real credential or customer is involved.

What it builds (the invariant being "a restore is only trustworthy if it is checked against something realistic"):
  * two organizations whose customers have identical names (tenant isolation must survive the round trip);
  * seven users with every role, one with memberships in both organizations, one allowed to create organizations;
  * credentials (real Argon2id hashes of throwaway passwords), a live session, a revoked session, an outstanding setup token;
  * invitations in every lifecycle state: pending, accepted (an existing account joined), revoked;
  * customers, items, a horse (with owner and stable), custom-field definitions with options and values;
  * a completed transaction invoiced, the invoice ISSUED, its PDF FROZEN (bytes in `invoice_pdfs`), in both organizations;
  * security events (the ones the actions above write, plus a login failure and a login success).
"""

import json
import os
import secrets
import sys
import uuid
from datetime import timedelta

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.core import clock
from app.core.db import SessionLocal
from app.core.tokens import hash_token, new_token
from app.models import AuthSession, CustomerType, Role, SecurityEvent, UserSetupToken
from app.modules.custom_fields.models import CustomFieldOption
from app.modules.invoicing.models import InvoicePdf
from app.main import app
from tests.auth_support import as_bff, make_credential, make_session
from tests.factories import add_member, make_customer, make_definition, make_horse, make_item, make_org, make_transaction, make_user, make_value

SENTINEL_ORG_A = "Sentinel Stable AB"
SENTINEL_ORG_B = "Sentinel Clinic AB"
SENTINEL_CUSTOMER = "Anna Sentinel"  # the same name in BOTH organizations on purpose
SENTINEL_HORSE = "Kalle Sentinel"
SENTINEL_EMAIL_DOMAIN = "restore-fixture.invalid"


def _user(db, label: str, *, credential: bool = False, **fields):
    user = make_user(db, email=f"{label}@{SENTINEL_EMAIL_DOMAIN}", name=f"{label.title()} Sentinel", **fields)
    if credential:
        make_credential(db, user, secrets.token_urlsafe(18))  # a throwaway password nobody knows or needs
    return user


def build_fixture() -> dict:
    client = TestClient(app)
    world: dict = {}
    with SessionLocal() as db:
        org_a = make_org(db, SENTINEL_ORG_A, legal_name="Sentinel Stable AB", address_line1="Storgatan 1", postal_code="903 26", city="Umeå", vat_number="SE556000000101")
        org_b = make_org(db, SENTINEL_ORG_B, legal_name="Sentinel Clinic AB", address_line1="Ridvägen 2", postal_code="903 30", city="Umeå")
        alice = _user(db, "alice", credential=True, max_owned_organizations=2)
        bob = _user(db, "bob", credential=True)
        carol, dave, frank = _user(db, "carol"), _user(db, "dave"), _user(db, "frank")
        erin = _user(db, "erin", credential=True)
        gina = _user(db, "gina", credential=True)  # joins organization A by invitation
        hana = _user(db, "hana")  # a user created by the operator who has not set a password yet
        for user, role in ((alice, Role.OWNER), (bob, Role.ADMIN), (carol, Role.ACCOUNTANT), (dave, Role.EMPLOYEE), (frank, Role.VIEWER)):
            add_member(db, org_a, user, role)
        add_member(db, org_b, erin, Role.OWNER)
        add_member(db, org_b, alice, Role.VIEWER)  # one person, two organizations, two roles
        now = clock.utcnow()
        live = make_session(db, alice)
        stale = make_session(db, bob, absolute_days=-1)  # expired
        stale.session.revoked_at, stale.session.revoked_reason = now, "logout"
        token = new_token()
        db.add(UserSetupToken(user_id=hana.id, token_hash=hash_token(token), purpose="set_password", created_at=now, expires_at=now + timedelta(hours=24)))
        db.add(SecurityEvent(occurred_at=now, event_type="login_success", actor_user_id=alice.id, source="203.0.113.7"))
        db.add(SecurityEvent(occurred_at=now, event_type="login_failure", source="203.0.113.8", identifier_hash=hash_token("restore-fixture-identifier")))

        ids: dict[str, dict] = {}
        for key, org, owner_user in (("a", org_a, alice), ("b", org_b, erin)):
            customer = make_customer(db, org, SENTINEL_CUSTOMER)
            stable = make_customer(db, org, f"Umeå HK {key.upper()}", CustomerType.COMPANY, f"hk-{key}@{SENTINEL_EMAIL_DOMAIN}", None)
            horse = make_horse(db, org, SENTINEL_HORSE, owner=customer, stable=stable)
            item = make_item(db, org, "Horse massage")
            make_item(db, org, "Hoof trimming", price_ex_vat="400.00", vat_rate="25.00")
            tx = make_transaction(db, org, billing_customer=stable, status="completed", lines=[{"item": item, "description": "Horse massage"}, {"description": "Travel", "unit_price_ex_vat": "50.00"}])
            owner_field = make_definition(db, org, key="owner", label="Owner", field_type="reference", reference_source="customer", show_on_invoice=True)
            kind = make_definition(db, org, key="kind", label="Kind", field_type="select", show_on_invoice=True, options=["Therapy", "Check"])
            note = make_definition(db, org, entity_type="transaction", key="po", label="PO", field_type="text", show_on_invoice=True)
            line_id = db.execute(select_first_line(tx.id)).scalar_one()
            option = db.scalar(select(CustomFieldOption).where(CustomFieldOption.definition_id == kind.id, CustomFieldOption.label == "Therapy"))
            make_value(db, org, owner_field, line_id, value_reference_id=customer.id)
            make_value(db, org, kind, line_id, value_option_id=option.id)
            make_value(db, org, note, tx.id, value_text=f"PO-{key.upper()}-17")
            ids[key] = {"org": org.id, "owner": owner_user.id, "customer": customer.id, "horse": horse.id, "transaction": tx.id}
        db.commit()
        world.update(
            org_a=str(org_a.id), org_b=str(org_b.id), alice=alice.email, erin=erin.email, bob=bob.email,
            session_token=live.token, session_user=alice.email, gina=gina.email, customer_a=str(ids["a"]["customer"]), customer_b=str(ids["b"]["customer"]),
            horse_a=str(ids["a"]["horse"]), setup_user=hana.email,
        )

    # Everything below goes through the real API, as the real people would use it.
    def headers(email, org):
        return as_bff({"X-Dev-User-Email": email, "X-Organization-Id": org})

    world["invoices"] = {}
    for key, owner_email, org in (("a", world["alice"], world["org_a"]), ("b", world["erin"], world["org_b"])):
        h = headers(owner_email, org)
        created = client.post("/api/invoices", json={"transaction_ids": [str(ids[key]["transaction"])]}, headers=h)
        assert created.status_code == 201, created.text
        invoice = created.json()
        issued = client.post(f"/api/invoices/{invoice['id']}/issue", headers={**h, "If-Match": f'"{invoice["version"]}"'})
        assert issued.status_code == 200, issued.text
        pdf = client.get(f"/api/invoices/{invoice['id']}/pdf", headers=h)  # the first read freezes the artifact
        assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF-"), pdf.text
        world["invoices"][key] = {"id": invoice["id"], "number_text": issued.json()["number_text"], "pdf_bytes": len(pdf.content)}

    ha = headers(world["alice"], world["org_a"])
    invitations = {}
    for label, address in (("pending", f"pending@{SENTINEL_EMAIL_DOMAIN}"), ("accepted", world["gina"]), ("revoked", f"revoked@{SENTINEL_EMAIL_DOMAIN}")):
        response = client.post("/api/invitations", json={"email": address, "role": "employee"}, headers=ha)
        assert response.status_code == 201, response.text
        invitations[label] = response.json()
    accepted = client.post("/api/invite/accept", json={"token": invitations["accepted"]["token"]}, headers=headers(world["gina"], world["org_a"]))
    assert accepted.status_code in (200, 201), accepted.text
    revoked = client.delete(f"/api/invitations/{invitations['revoked']['id']}", headers=ha)
    assert revoked.status_code == 204, revoked.text
    world["invitations"] = {label: body["id"] for label, body in invitations.items()}

    with SessionLocal() as db:
        world["pdf_rows"] = len(list(db.scalars(select(InvoicePdf.id))))
    return world


def select_first_line(transaction_id: uuid.UUID):
    from app.modules.sales.models import TransactionLine

    return select(TransactionLine.id).where(TransactionLine.transaction_id == transaction_id, TransactionLine.position == 1)


def main() -> int:
    if not os.environ.get("DATABASE_URL"):
        print("DATABASE_URL (a disposable, migrated database) is required", file=sys.stderr)
        return 2
    print(json.dumps(build_fixture()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
