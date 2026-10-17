from app.core.entity_registry import EntityType, FilterSpec, ReferenceSpec, Registry
from app.modules.equine.models import Horse


def register(registry: Registry) -> None:
    """Make horses referenceable from custom fields.

    `filters` tells generic capabilities which columns of a horse point at another
    registered entity, so a dependent field can narrow its choices ("horses whose
    owner is the selected customer") without knowing anything about horses.
    """
    registry.register(
        EntityType(
            key="horse",
            label="Horse",
            model=Horse,
            reference=ReferenceSpec(
                label_column="name",
                active_column="active",
                search_columns=("name",),
                filters={
                    "owner_customer_id": FilterSpec(
                        column="owner_customer_id", references="customer", label="Owner"
                    ),
                    "stable_customer_id": FilterSpec(
                        column="stable_customer_id", references="customer", label="Stable"
                    ),
                },
            ),
            service_subject=True,  # a treatment performed on a horse
        )
    )
