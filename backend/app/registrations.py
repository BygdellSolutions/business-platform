"""Entity registrations for the standard (non-module) records. Called from main.py."""

from app.core.entity_registry import EntityType, ReferenceSpec, Registry
from app.models import Customer, Supplier


def register(registry: Registry) -> None:
    registry.register(
        EntityType(
            key="customer",
            label="Customer",
            model=Customer,
            reference=ReferenceSpec(
                label_column="name",
                active_column="active",
                search_columns=("name", "email"),
            ),
            service_subject=True,  # a service performed for a person
        )
    )
    registry.register(
        EntityType(
            key="supplier",
            label="Supplier",
            model=Supplier,
            reference=ReferenceSpec(label_column="name", active_column="active", search_columns=("name", "email")),
        )
    )
