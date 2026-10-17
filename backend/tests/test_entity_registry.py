"""The core registry: registration rules, fail-fast validation, isolation for tests, and
what the application's modules actually registered (the registrations contract)."""

import pytest

from app.core.entity_registry import (
    EntityType,
    FilterSpec,
    ParentSpec,
    ReferenceSpec,
    Registry,
    RegistryError,
    registry,
)
from app.models import Customer, Item
from app.modules.equine.models import Horse
from app.modules.sales.models import Transaction, TransactionLine


def entity(key="thing", model=Customer, **kwargs) -> EntityType:
    return EntityType(key=key, label=key.title(), model=model, **kwargs)


# --- registration rules ----------------------------------------------------------------------------


def test_register_and_look_up():
    r = Registry()
    r.register(entity("customer", Customer, reference=ReferenceSpec(search_columns=("name",))))

    assert r.get("customer").label == "Customer"
    assert [e.key for e in r.all()] == ["customer"]
    assert r.key_for_model(Customer) == "customer" and r.key_for_model(Item) is None


@pytest.mark.parametrize("key", ["", "Customer", "1customer", "has space", "x" * 65, "dash-ed"])
def test_invalid_keys_are_refused(key):
    with pytest.raises(RegistryError, match="invalid entity key"):
        Registry().register(entity(key))


def test_duplicate_keys_are_refused():
    r = Registry()
    r.register(entity("customer"))
    with pytest.raises(RegistryError, match="already registered"):
        r.register(entity("customer", Item))


def test_only_tenant_owned_models_can_be_registered():
    class NotTenantOwned:
        pass

    with pytest.raises(RegistryError, match="TenantOwned"):
        Registry().register(entity("thing", NotTenantOwned))  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "spec",
    [
        ReferenceSpec(label_column="no_such_column"),
        ReferenceSpec(active_column="no_such_column"),
        ReferenceSpec(search_columns=("name", "no_such_column")),
        ReferenceSpec(filters={"f": FilterSpec(column="no_such_column", references="customer", label="X")}),
    ],
    ids=["label", "active", "search", "filter"],
)
def test_a_misspelled_column_fails_at_registration(spec):
    with pytest.raises(RegistryError, match="no_such_column"):
        Registry().register(entity("thing", Customer, reference=spec))


def test_a_reference_needs_a_search_column():
    with pytest.raises(RegistryError, match="search column"):
        Registry().register(entity("thing", Customer, reference=ReferenceSpec(search_columns=())))


def test_a_misspelled_parent_column_fails_at_registration():
    with pytest.raises(RegistryError, match="no_such_column"):
        Registry().register(entity("line", TransactionLine, parent=ParentSpec("transaction", "no_such_column")))


# --- cross-entity validation (after every module has registered) ------------------------------------------


def test_validate_catches_an_unknown_parent():
    r = Registry()
    r.register(entity("line", TransactionLine, parent=ParentSpec("transaction", "transaction_id")))
    with pytest.raises(RegistryError, match="unknown parent"):
        r.validate()


@pytest.mark.parametrize("target", ["nonsense", "plain"])
def test_validate_catches_filters_that_reference_nothing_referenceable(target):
    r = Registry()
    r.register(entity("plain", Item))  # registered, but not referenceable
    r.register(
        entity(
            "horse",
            Horse,
            reference=ReferenceSpec(filters={"owner": FilterSpec("owner_customer_id", target, "Owner")}),
        )
    )
    with pytest.raises(RegistryError, match="not a registered referenceable"):
        r.validate()


def test_validate_passes_for_consistent_registrations():
    r = Registry()
    r.register(entity("customer", Customer, reference=ReferenceSpec()))
    r.register(
        entity("horse", Horse, reference=ReferenceSpec(filters={"owner": FilterSpec("owner_customer_id", "customer", "Owner")}))
    )
    r.register(entity("transaction", Transaction, custom_fields=True))
    r.register(entity("line", TransactionLine, custom_fields=True, parent=ParentSpec("transaction", "transaction_id")))

    r.validate()

    assert [e.key for e in r.children_of("transaction")] == ["line"]
    assert [e.key for e in r.custom_field_types()] == ["transaction", "line"]


# --- test isolation -------------------------------------------------------------------------------------------


def test_isolated_registrations_leave_no_trace():
    before = [e.key for e in registry.all()]
    guards, validators = len(registry._reference_guards), len(registry.validators)

    with registry.isolated():
        registry.register(entity("scratch", Item))
        registry.add_reference_guard(lambda *a: True)
        registry.add_validator(lambda *a: [])
        assert registry.get("scratch") is not None

    assert [e.key for e in registry.all()] == before
    assert (len(registry._reference_guards), len(registry.validators)) == (guards, validators)


def test_isolated_restores_even_when_the_block_fails():
    with pytest.raises(RuntimeError):
        with registry.isolated():
            registry.register(entity("scratch", Item))
            raise RuntimeError("boom")

    assert registry.get("scratch") is None


# --- what the application's modules registered -------------------------------------------------------------------------


def test_the_application_registered_its_entities_explicitly():
    keys = {e.key: e for e in registry.all()}

    assert set(keys) == {"customer", "horse", "supplier", "transaction", "transaction_line"}
    assert keys["customer"].model is Customer and keys["customer"].reference is not None
    assert keys["horse"].model is Horse
    assert keys["transaction"].custom_fields and keys["transaction_line"].custom_fields
    assert keys["customer"].custom_fields is False and keys["horse"].custom_fields is False


def test_horses_declare_which_of_their_columns_point_at_customers():
    filters = registry.get("horse").reference.filters

    assert {k: (f.column, f.references) for k, f in filters.items()} == {
        "owner_customer_id": ("owner_customer_id", "customer"),
        "stable_customer_id": ("stable_customer_id", "customer"),
    }


def test_transaction_lines_are_children_of_transactions_and_lockable():
    line = registry.get("transaction_line")

    assert (line.parent.entity, line.parent.column) == ("transaction", "transaction_id")
    assert line.is_editable is not None and registry.get("transaction").is_editable is not None
    assert [e.key for e in registry.children_of("transaction")] == ["transaction_line"]


def test_the_custom_fields_capability_registered_its_guard_and_validator():
    assert len(registry._reference_guards) >= 1
    assert len(registry.validators) >= 1
    assert registry.validate() is None  # the whole registry is consistent


def test_every_registered_model_is_known_to_the_metadata_and_mapped():
    # a registration that names a model without a table would only fail at request time
    for e in registry.all():
        assert e.model.__table__.name
