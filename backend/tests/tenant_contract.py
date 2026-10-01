"""Registry of tenant-owned resources that must satisfy the tenant-isolation contract.

`test_tenant_isolation_contract.py` runs the same cross-tenant checks against every
entry here (CLAUDE.md sections 6 and 23). To put a new tenant-owned resource
under the contract, add one `Resource` to RESOURCES; no new test code is needed.

Contract requirements for a resource:
- `make(db, org, **overrides)` creates a record whose defaults look IDENTICAL in every
  organization (same names, same values) so a missing organization filter is visible.
- the API lives at `path` with POST / GET list+search (`?q=`) / GET, PATCH, DELETE `/{id}`.
- `create_body` is a dict, or a function `(db, org) -> dict` for resources whose valid body
  contains references (ids) that must belong to the acting organization.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.models import Customer, Item, Organization
from app.modules.equine.models import Horse
from tests.factories import make_customer, make_horse, make_item


@dataclass(frozen=True)
class Resource:
    name: str
    path: str
    model: type
    make: Callable[..., Any]
    create_body: dict[str, Any] | Callable[[Session, Organization], dict[str, Any]]
    # ^ a valid POST body for the acting organization (must not contain organization_id)
    patch_body: dict[str, Any]  # a valid PATCH body changing exactly one field
    patch_field: str  # the model attribute that patch_body changes
    patch_value: Any  # its value after the patch
    twin_search: str  # `?q=` text matching the identical-looking record in both orgs
    unique_overrides: dict[str, Any]  # make() overrides giving a record only `unique_search` finds
    unique_search: str


def body_for(resource: Resource, db: Session, org: Organization) -> dict[str, Any]:
    """A valid create body for `resource`, built for the acting organization."""
    if callable(resource.create_body):
        return resource.create_body(db, org)
    return dict(resource.create_body)


CUSTOMERS = Resource(
    name="customers",
    path="/api/customers",
    model=Customer,
    make=make_customer,
    create_body={"customer_type": "person", "name": "Contract Created"},
    patch_body={"phone": "999"},
    patch_field="phone",
    patch_value="999",
    twin_search="anna",
    unique_overrides={"name": "Zelda Stable", "email": "zelda@example.test"},
    unique_search="zelda",
)

ITEMS = Resource(
    name="items",
    path="/api/items",
    model=Item,
    make=make_item,
    create_body={
        "type": "service",
        "name": "Contract Created",
        "unit": "session",
        "price_ex_vat": "10.00",
        "vat_rate": "25.00",
    },
    patch_body={"unit": "hour"},
    patch_field="unit",
    patch_value="hour",
    twin_search="massage",
    unique_overrides={"name": "Zelda Saddle Pad"},
    unique_search="zelda",
)


def _horse_create_body(db: Session, org: Organization) -> dict[str, Any]:
    owner = make_customer(db, org, "Contract Owner")
    return {"name": "Contract Created", "owner_customer_id": str(owner.id)}


HORSES = Resource(
    name="horses",
    path="/api/horses",
    model=Horse,
    make=make_horse,
    create_body=_horse_create_body,
    patch_body={"name": "Renamed"},
    patch_field="name",
    patch_value="Renamed",
    twin_search="kalle",
    unique_overrides={"name": "Zelda"},
    unique_search="zelda",
)

RESOURCES = [CUSTOMERS, ITEMS, HORSES]
