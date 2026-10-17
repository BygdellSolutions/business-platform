"""The stock figures of a long catalogue: the answers are the same whether the items are asked for at once or in short statements, and the
number of statements grows only with chunks of 60 items, never with the movements or orders behind them."""

import uuid
from decimal import Decimal

from sqlalchemy import event, text
from sqlalchemy.orm import Session

from app.core.tenant import TenantContext
from app.models import ItemType, Role
from app.modules.inventory import api as inventory_api
from app.modules.inventory import service
from tests.factories import add_member, make_item, make_org, make_user


def _world(db: Session, items: int, movements_each: int):
    org = make_org(db, f"Scale {items}")
    user = make_user(db)
    add_member(db, org, user, Role.OWNER)
    ids = []
    for index in range(items):
        item = make_item(db, org, name=f"Product {index}", type=ItemType.PRODUCT, unit="pcs", track_stock=True, low_stock_threshold=Decimal("3"))
        ids.append(item.id)
    db.flush()
    # a long ledger per item, written in one statement (the ledger refuses nothing here: every row follows the one before)
    db.execute(
        text(
            """
            INSERT INTO stock_movements (organization_id, item_id, quantity_change, quantity_before, quantity_after, reason, note)
            SELECT :o, i, 1, n - 1, n, 'adjustment', 'x' FROM unnest(CAST(:items AS uuid[])) AS i, generate_series(1, :n) AS n
            """
        ),
        {"o": org.id, "items": [str(i) for i in ids], "n": movements_each},
    )
    db.execute(text("ANALYZE stock_movements"))
    return TenantContext(user=user, organization_id=org.id, role=Role.OWNER), ids


def _statements(db: Session, call):
    counted: list[str] = []
    listener = lambda conn, cursor, statement, parameters, context, many: counted.append(statement)  # noqa: E731
    engine = db.get_bind().engine
    event.listen(engine, "before_cursor_execute", listener)
    try:
        result = call()
    finally:
        event.remove(engine, "before_cursor_execute", listener)
    return result, len(counted)


def test_the_figures_are_the_same_asked_for_all_at_once_or_in_chunks(db_session: Session):
    ctx, ids = _world(db_session, items=130, movements_each=4)  # more than two chunks of 60
    stock = service.on_hand(db_session, ctx.organization_id, ids)
    assert len(stock) == 130 and set(stock.values()) == {Decimal("4.000")}  # the latest movement of each item
    assert service.committed(db_session, ctx.organization_id, ids) == {i: Decimal(0) for i in ids}
    assert service.incoming(db_session, ctx.organization_id, ids) == {i: Decimal(0) for i in ids}
    assert service.allocated(db_session, ctx.organization_id, ids) == {i: Decimal(0) for i in ids}
    assert list(service.on_hand(db_session, ctx.organization_id, [uuid.uuid4()]).values()) == [Decimal(0)]  # an unknown item is simply zero
    assert service.on_hand(db_session, ctx.organization_id, []) == {}


def test_the_summary_issues_the_same_statements_for_a_long_ledger_as_for_a_short_one(db_session: Session):
    short_ctx, _ = _world(db_session, items=40, movements_each=2)
    _, short = _statements(db_session, lambda: inventory_api.inventory_summary(ctx=short_ctx, db=db_session))
    long_ctx, _ = _world(db_session, items=40, movements_each=400)  # 16,000 movements
    summary, long = _statements(db_session, lambda: inventory_api.inventory_summary(ctx=long_ctx, db=db_session))
    assert summary.tracked_items == 40
    assert long == short  # the history behind the figures costs no extra statement


def test_the_stock_overview_of_a_long_catalogue_grows_only_with_chunks_of_items(db_session: Session):
    small_ctx, _ = _world(db_session, items=50, movements_each=3)
    _, small = _statements(db_session, lambda: inventory_api.list_stock_items(q=None, state=None, include_inactive=False, sort=_no_sort(), ctx=small_ctx, db=db_session))
    big_ctx, _ = _world(db_session, items=300, movements_each=3)
    rows, big = _statements(db_session, lambda: inventory_api.list_stock_items(q=None, state=None, include_inactive=False, sort=_no_sort(), ctx=big_ctx, db=db_session))
    assert len(rows) == 300
    # 50 items are one chunk of 60, 300 items are five: every further chunk costs the same 8 statements (measured: 9, 17, 25, 41, 73 for 50, 120, 180, 300, 600 items),
    # whatever the movements and orders behind the figures. Linear in the chunks, never in the history.
    assert big == small + 8 * (5 - 1)


def _no_sort():
    from app.api.deps import Sorting

    return Sorting(key=None, descending=False)
