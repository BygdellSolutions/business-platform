"""Registry of the entity types that modules expose to generic capabilities.

A module registers its own entity types here (explicitly, from main.py). Generic
capabilities (custom fields today; others later) read the registry instead of importing
modules, so neither side depends on the other:

    modules (customers, catalog, sales, domain modules) ──► core registry ◄── custom fields

What a module can say about an entity type:

* `custom_fields`  - organizations may add custom fields to it
* `reference`      - other records may point at it (reference fields); the spec names the
                     columns used for display, search and the filters a dependent field
                     may apply (`FilterSpec`: "column X of this type points to type Y")
* `parent`         - it belongs to another entity type through a foreign-key column, so
                     generic checks can reach it from its parent
* `is_editable`    - a callback that answers "may this record's custom values change now?".
                     Called inside the writing transaction; an implementation that needs to
                     serialize with other writers takes a row lock on the row that decides it.

Reference guards ("is this record pointed at from somewhere a foreign key cannot see?") and
lifecycle validators (generic "may this transition happen?" checks, see
`app.core.lifecycle`) are registered here too. `registry.validate()` fails fast at startup
on a typo in a column name or an unknown entity key.
"""

import re
import uuid
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.core.tenant import TenantContext
from app.models.mixins import TenantOwned

KEY_RE = re.compile(r"[a-z][a-z0-9_]{0,63}")


class RegistryError(Exception):
    """A module registered something inconsistent. Raised at startup, not per request."""


@dataclass(frozen=True)
class FilterSpec:
    """`column` of this entity points at entity type `references` (e.g. a horse's owner)."""

    column: str
    references: str
    label: str


@dataclass(frozen=True)
class ReferenceSpec:
    label_column: str = "name"
    active_column: str | None = "active"
    search_columns: tuple[str, ...] = ("name",)
    filters: Mapping[str, FilterSpec] = field(default_factory=dict)


@dataclass(frozen=True)
class ParentSpec:
    entity: str  # registry key of the parent entity type
    column: str  # column on this entity's model holding the parent's id


IsEditable = Callable[[Session, TenantContext, uuid.UUID], bool]
# (db, entity_key, organization_id, record_id) -> True if something still points at the record
ReferenceGuard = Callable[[Session, str, uuid.UUID, uuid.UUID], bool]
# (db, ctx, event, entity_key, entity_id) -> list of lifecycle Problems
LifecycleValidator = Callable[[Session, TenantContext, str, str, uuid.UUID], list[Any]]
# (db, ctx, event, entity_key, entity_id): acts after a lifecycle step, inside its database transaction; never commits.
LifecycleEffect = Callable[[Session, TenantContext, str, str, uuid.UUID], None]
# (db, organization_id) -> a human reason if stored prices make a currency change unsafe, else None
CurrencyGuard = Callable[[Session, uuid.UUID], str | None]


@dataclass(frozen=True)
class EntityType:
    key: str
    label: str
    model: type[TenantOwned]
    custom_fields: bool = False
    reference: ReferenceSpec | None = None
    parent: ParentSpec | None = None
    is_editable: IsEditable | None = None
    # A service can be performed FOR a record of this type (a person, an animal, a vehicle...). Needs `reference`
    # (the label shown on lines and invoices comes from it).
    service_subject: bool = False


class Registry:
    def __init__(self) -> None:
        self._entities: dict[str, EntityType] = {}
        self._reference_guards: list[ReferenceGuard] = []
        self._validators: list[LifecycleValidator] = []
        self._effects: list[LifecycleEffect] = []
        self._hooks: dict[str, list[Callable[..., Any]]] = {}
        self._currency_guards: list[CurrencyGuard] = []

    # --- entity types ---------------------------------------------------------------------

    def register(self, entity: EntityType) -> None:
        if not KEY_RE.fullmatch(entity.key):
            raise RegistryError(f"invalid entity key {entity.key!r}")
        if entity.key in self._entities:
            raise RegistryError(f"entity type {entity.key!r} is already registered")
        if not (isinstance(entity.model, type) and issubclass(entity.model, TenantOwned)):
            raise RegistryError(f"{entity.key}: model must be a TenantOwned model")
        if entity.reference is not None:
            spec = entity.reference
            columns = [spec.label_column, *spec.search_columns]
            columns += [spec.active_column] if spec.active_column else []
            columns += [f.column for f in spec.filters.values()]
            self._require_columns(entity, columns)
            if not spec.search_columns:
                raise RegistryError(f"{entity.key}: a reference needs at least one search column")
        if entity.parent is not None:
            self._require_columns(entity, [entity.parent.column])
        if entity.service_subject and entity.reference is None:
            raise RegistryError(f"{entity.key}: a service subject must be referenceable")
        self._entities[entity.key] = entity

    @staticmethod
    def _require_columns(entity: EntityType, columns: list[str]) -> None:
        missing = [c for c in columns if not hasattr(entity.model, c)]
        if missing:
            raise RegistryError(f"{entity.key}: model has no column(s) {missing}")

    def validate(self) -> None:
        """Cross-entity checks; call once after every module has registered."""
        for entity in self._entities.values():
            if entity.parent is not None and entity.parent.entity not in self._entities:
                raise RegistryError(f"{entity.key}: unknown parent entity {entity.parent.entity!r}")
            if entity.reference is not None:
                for name, spec in entity.reference.filters.items():
                    target = self._entities.get(spec.references)
                    if target is None or target.reference is None:
                        raise RegistryError(
                            f"{entity.key}: filter {name!r} references {spec.references!r}, "
                            "which is not a registered referenceable entity"
                        )

    def get(self, key: str) -> EntityType | None:
        return self._entities.get(key)

    def all(self) -> list[EntityType]:
        return list(self._entities.values())

    def service_subjects(self) -> list[EntityType]:
        return [e for e in self._entities.values() if e.service_subject]

    def custom_field_types(self) -> list[EntityType]:
        return [e for e in self._entities.values() if e.custom_fields]

    def children_of(self, key: str) -> list[EntityType]:
        return [e for e in self._entities.values() if e.parent and e.parent.entity == key]

    def key_for_model(self, model: type) -> str | None:
        for entity in self._entities.values():
            if entity.model is model:
                return entity.key
        return None

    # --- polymorphic reference protection -----------------------------------------------------

    def add_reference_guard(self, guard: ReferenceGuard) -> None:
        self._reference_guards.append(guard)

    def is_referenced(self, db: Session, record: Any) -> bool:
        """True if a registered guard says something points at `record`."""
        key = self.key_for_model(type(record))
        if key is None:
            return False
        return any(guard(db, key, record.organization_id, record.id) for guard in self._reference_guards)

    # --- lifecycle validators ------------------------------------------------------------------

    def add_validator(self, validator: LifecycleValidator) -> None:
        self._validators.append(validator)

    @property
    def validators(self) -> list[LifecycleValidator]:
        return list(self._validators)

    # --- named hooks ------------------------------------------------------------------------------
    #
    # A capability one module offers to others by NAME, so the caller never imports the provider: Invoicing asks
    # "stock.tracked_lines" and calls "stock.return" when a credit note says goods came back; Inventory answers.
    # Without a provider the call does nothing (an organization without Inventory still credits).

    def add_hook(self, name: str, handler: Callable[..., Any]) -> None:
        self._hooks.setdefault(name, []).append(handler)

    def call_hooks(self, name: str, *args: Any, **kwargs: Any) -> list[Any]:
        """Every handler's answer, in registration order (an empty list when nobody provides the hook)."""
        return [handler(*args, **kwargs) for handler in self._hooks.get(name, [])]

    # --- lifecycle effects (see app.core.lifecycle.run_effects) ---------------------------------

    def add_effect(self, effect: LifecycleEffect) -> None:
        self._effects.append(effect)

    @property
    def effects(self) -> list[LifecycleEffect]:
        return list(self._effects)

    # --- currency guards -----------------------------------------------------------------------

    def add_currency_guard(self, guard: CurrencyGuard) -> None:
        self._currency_guards.append(guard)

    def currency_lock_reason(self, db: Session, organization_id: uuid.UUID) -> str | None:
        """Why the organization's default currency may not change now, or None if it may.

        A guard answers for the records it owns whose prices are only meaningful in the current
        currency (items, transactions, ...). Modules register guards; core never imports them.
        """
        for guard in self._currency_guards:
            reason = guard(db, organization_id)
            if reason:
                return reason
        return None

    # --- tests ---------------------------------------------------------------------------------

    @contextmanager
    def isolated(self) -> Iterator["Registry"]:
        """Let a test register extra entities/guards/validators and leave no trace."""
        saved = (
            dict(self._entities),
            list(self._reference_guards),
            list(self._validators),
            list(self._currency_guards),
        )
        try:
            yield self
        finally:
            self._entities = dict(saved[0])
            self._reference_guards = list(saved[1])
            self._validators = list(saved[2])
            self._currency_guards = list(saved[3])


registry = Registry()
