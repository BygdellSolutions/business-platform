"""The suppliers migration: every typed supplier name on incoming stock becomes one supplier of its organization, the
deliveries point to it, and the downgrade puts the names back.
"""

import uuid

from tests.test_migration_currency import _alembic, _downgrade, _execute, _scalar, _upgrade, scratch_url  # noqa: F401

BEFORE = "a0c2e4f6b891"
SUPPLIERS = "b1d3f5a7c902"


def _delivery(url: str, org: uuid.UUID, item: uuid.UUID, supplier: str | None) -> uuid.UUID:
    row = uuid.uuid4()
    _execute(
        url,
        "insert into incoming_stock (id, organization_id, item_id, quantity, supplier) values (:r, :o, :i, 1, :s)",
        r=row, o=org, i=item, s=supplier,
    )
    return row


def test_typed_names_become_suppliers_per_organization_and_come_back_on_downgrade(scratch_url: str):
    _upgrade(scratch_url, BEFORE)
    orgs, items = [uuid.uuid4(), uuid.uuid4()], [uuid.uuid4(), uuid.uuid4()]
    for org, item, name in zip(orgs, items, ("Org A", "Org B")):
        _execute(scratch_url, "insert into organizations (id, name, default_currency) values (:o, :n, 'SEK')", o=org, n=name)
        _execute(
            scratch_url,
            "insert into items (id, organization_id, type, name, unit, price_ex_vat, vat_rate, track_stock) "
            "values (:i, :o, 'product', 'Liniment', 'pcs', 1, 25, true)",
            i=item, o=org,
        )
    first = _delivery(scratch_url, orgs[0], items[0], "Horse Supplies AB")
    same = _delivery(scratch_url, orgs[0], items[0], "  horse supplies ab ")
    other = _delivery(scratch_url, orgs[0], items[0], "Feed Co")
    none = _delivery(scratch_url, orgs[0], items[0], None)
    foreign = _delivery(scratch_url, orgs[1], items[1], "Horse Supplies AB")

    _upgrade(scratch_url, SUPPLIERS)

    assert _scalar(scratch_url, "select count(*) from suppliers where organization_id = :o", o=orgs[0]) == 2
    assert _scalar(scratch_url, "select count(*) from suppliers where organization_id = :o", o=orgs[1]) == 1

    def supplier_of(row):
        return _scalar(scratch_url, "select s.name from incoming_stock i join suppliers s on s.id = i.supplier_id where i.id = :r", r=row)

    assert supplier_of(first) == supplier_of(same) == "Horse Supplies AB"
    assert supplier_of(other) == "Feed Co"
    assert _scalar(scratch_url, "select supplier_id from incoming_stock where id = :r", r=none) is None
    a_supplier = _scalar(scratch_url, "select supplier_id from incoming_stock where id = :r", r=first)
    assert _scalar(scratch_url, "select supplier_id from incoming_stock where id = :r", r=foreign) != a_supplier  # never shared across tenants

    _downgrade(scratch_url, BEFORE)
    assert _scalar(scratch_url, "select supplier from incoming_stock where id = :r", r=same) == "Horse Supplies AB"
    assert _scalar(scratch_url, "select supplier from incoming_stock where id = :r", r=foreign) == "Horse Supplies AB"
    assert _scalar(scratch_url, "select supplier from incoming_stock where id = :r", r=none) is None
