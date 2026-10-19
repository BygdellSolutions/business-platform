"""The dashboard and the "ready to invoice" list ask the database about the whole backlog with SUBQUERIES instead of lists of ids
(a constant number of statements however large the backlog). That is an optimisation only: it must give exactly the figures
the id-list way gives, for every kind of order: not delivered in full, partly invoiced, waiting, untracked, nothing to invoice."""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.core.tenant import TenantContext
from app.models import Role
from app.modules.invoicing import api as invoicing_api
from app.modules.invoicing import delivery
from app.modules.sales.models import Transaction
from tests.factories import make_user
from tests.invoicing_support import INVOICES, if_match, issue
from tests.test_invoicing_by_delivery import _deliver_later, _invoice, _order, _stock, shop  # noqa: F401  (shop is a fixture)


@pytest.fixture
def backlog(client: TestClient, shop, db_session: Session):
    """A mixed backlog in one organization: every state the three side queries can see."""
    _stock(client, shop, "10")
    partly = _order(client, shop, (shop.pad, "12"))  # 10 handed over, 2 backordered
    issue(client, shop.owner, _invoice(client, shop, partly, partial=True).json())  # the 10 are invoiced and issued
    _deliver_later(client, shop, "2")  # the 2 arrive and go onto a draft by themselves ...
    for draft in client.get(INVOICES, params={"status": "draft"}, headers=shop.owner).json():
        assert client.delete(f"{INVOICES}/{draft['id']}", headers={**shop.owner, **if_match(draft["version"])}).status_code == 204  # ... thrown away: 2 are ready again
    nothing_delivered = _order(client, shop, (shop.pad, "3"))  # no stock left: all 3 backordered, nothing to invoice
    waiting = _order(client, shop, (shop.pad, "2"), (shop.massage, "1"))
    client.post(f"{INVOICES}/holds", json={"transaction_id": waiting["id"]}, headers=shop.owner)  # waits until everything is delivered
    untracked = _order(client, shop, (shop.massage, "2"), (shop.massage, "3"))
    on_a_draft = _order(client, shop, (shop.massage, "1"))
    assert _invoice(client, shop, on_a_draft).status_code == 200  # reserved by a draft invoice: not in the backlog at all
    return partly, nothing_delivered, waiting, untracked


def _ctx(shop, db: Session) -> TenantContext:
    return TenantContext(user=make_user(db), organization_id=shop.org.id, role=Role.OWNER)


def _same(a: dict, b: dict) -> bool:
    shape = lambda parts: {order: [(p.line.id, p.quantity, p.net_amount, p.vat_amount, p.gross_amount, p.backordered_quantity) for p in items] for order, items in parts.items()}  # noqa: E731
    return shape(a) == shape(b)


def test_the_subquery_way_gives_exactly_the_figures_of_the_id_list_way(client, shop, backlog, db_session: Session):
    ctx = _ctx(shop, db_session)
    orders = list(db_session.scalars(invoicing_api.scoped_select(Transaction, ctx).where(*invoicing_api._ready_conditions())))
    assert len(orders) >= 3  # control: a real backlog is being compared
    partly = backlog[0]

    by_ids = invoicing_api._parts_of(db_session, ctx, orders)
    by_scope = invoicing_api._parts_of(db_session, ctx, orders, scope=invoicing_api._ready_scope(ctx))

    assert by_ids and _same(by_ids, by_scope)
    figures = {str(order): sum((p.quantity for p in items), Decimal("0")) for order, items in by_scope.items()}
    # The controls: the partly invoiced order offers exactly its 2 later-delivered units (12 delivered - 10 invoiced); the order with
    # nothing delivered and the waiting one offer nothing; the untracked one offers all of it.
    assert figures[partly["id"]] == Decimal("2")
    assert backlog[1]["id"] not in figures and backlog[2]["id"] not in figures
    assert figures[backlog[3]["id"]] == Decimal("5")


def test_the_list_filters_narrow_the_scope_exactly_like_they_narrow_the_orders(client, shop, backlog, db_session: Session):
    ctx = _ctx(shop, db_session)
    other = shop.anna
    # a filter that matches nothing and one that matches everything: the scope follows the list, not the whole backlog
    nothing = [Transaction.billing_customer_id == shop.org.id]  # no customer has the organization's id
    assert db_session.scalars(invoicing_api._ready_scope(ctx, *nothing)).all() == []
    everything = invoicing_api._ready_scope(ctx, Transaction.billing_customer_id == other.id)
    assert len(db_session.scalars(everything).all()) == len(db_session.scalars(invoicing_api._ready_scope(ctx)).all())


def test_the_number_of_statements_does_not_grow_with_the_backlog(client, shop, backlog, db_session: Session):
    ctx = _ctx(shop, db_session)
    engine = db_session.get_bind().engine
    counted: list[str] = []
    listener = lambda conn, cursor, statement, parameters, context, executemany: counted.append(statement)  # noqa: E731

    def statements_for(orders_wanted: int) -> int:
        orders = list(db_session.scalars(invoicing_api.scoped_select(Transaction, ctx).where(*invoicing_api._ready_conditions())))[:orders_wanted]
        scope = select(Transaction.id).where(Transaction.id.in_([order.id for order in orders]))  # exactly these orders (the contract of scope)
        counted.clear()
        event.listen(engine, "before_cursor_execute", listener)
        try:
            invoicing_api._parts_of(db_session, ctx, orders, scope=scope)
        finally:
            event.remove(engine, "before_cursor_execute", listener)
        return len(counted)

    assert statements_for(1) == statements_for(10)  # one order or the whole backlog: the same few statements
    assert statements_for(10) <= 5
