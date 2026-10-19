"""Custom-field rules: definitions, choices, value validation and storage, and the two
generic hooks this capability registers (required-field validator, reference guard).

Everything is expressed through the core registry. This file never names a concrete
entity: which entities exist, how they display, which filters a dependent field may use
and when a record may change all come from what modules registered.
"""

import re
import uuid
from collections.abc import Iterable, Sequence
from datetime import date
from decimal import Decimal
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core import audit
from app.core.entity_registry import EntityType, registry
from app.core.lifecycle import COMPLETE, Problem
from app.core.query import contains_pattern
from app.core.tenant import TenantContext
from app.core.tenant_scope import create_scoped, get_scoped, get_scoped_or_404, scoped_select
from app.modules.custom_fields.models import (
    TEXT_MAX_LENGTH,
    CustomFieldDefinition,
    CustomFieldOption,
    CustomFieldValue,
    FieldType,
)
from app.modules.custom_fields.schemas import (
    ChoiceRead,
    DefinitionCreate,
    DefinitionRead,
    OptionCreate,
    OptionRead,
    OptionUpdate,
    ReferenceConfig,
    ReferenceRead,
    ValueRead,
)

UNIQUE_VIOLATION = "23505"
POSITION_STEP = 10

VALUE_COLUMN = {
    FieldType.TEXT: "value_text",
    FieldType.NUMBER: "value_number",
    FieldType.DATE: "value_date",
    FieldType.BOOLEAN: "value_boolean",
    FieldType.SELECT: "value_option_id",
    FieldType.REFERENCE: "value_reference_id",
}
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
NUMBER_RE = re.compile(r"-?\d{1,14}(\.\d{1,4})?")  # NUMERIC(18,4)
NUMBER_HINT = 'send numbers as a decimal string such as "12.5", not as a JSON number with decimals'


def error(status_code: int, loc: Sequence[str | int], message: str, error_type: str) -> HTTPException:
    return HTTPException(
        status_code, detail=[{"loc": ["body", *loc], "msg": message, "type": error_type}]
    )


def unprocessable(errors: list[dict[str, Any]]) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=errors)


def err(loc: Sequence[str | int], message: str, error_type: str) -> dict[str, Any]:
    return {"loc": ["body", *loc], "msg": message, "type": error_type}


def custom_field_entity(entity_type: str) -> EntityType:
    """A registered entity type that accepts custom fields, or 404."""
    entity = registry.get(entity_type)
    if entity is None or not entity.custom_fields:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")
    return entity


# --- definitions --------------------------------------------------------------------------------


# The yes/no properties of a definition that a caller may filter by ("every field flagged X").
# The filter is generic: it names a property, not who wants it or why.
FLAGS = ("required", "show_in_form", "show_in_table", "show_on_invoice")


def load_definitions(
    db: Session,
    ctx: TenantContext,
    entity_type: str | None = None,
    *,
    include_disabled: bool = True,
    flag: str | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> list[CustomFieldDefinition]:
    """Definitions of the active organization. `flag` keeps only those whose yes/no property of
    that name (one of FLAGS) is true."""
    query = scoped_select(CustomFieldDefinition, ctx)
    if entity_type is not None:
        query = query.where(CustomFieldDefinition.entity_type == entity_type)
    if not include_disabled:
        query = query.where(CustomFieldDefinition.enabled.is_(True))
    if flag is not None:
        if flag not in FLAGS:
            raise ValueError(f"unknown definition flag {flag!r}")
        query = query.where(getattr(CustomFieldDefinition, flag).is_(True))
    query = query.order_by(
        CustomFieldDefinition.entity_type, CustomFieldDefinition.position, CustomFieldDefinition.key
    ).offset(offset)
    if limit is not None:
        query = query.limit(limit)
    return list(db.scalars(query))


def options_by_definition(
    db: Session, ctx: TenantContext, definition_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, list[CustomFieldOption]]:
    grouped: dict[uuid.UUID, list[CustomFieldOption]] = {}
    ids = list(definition_ids)
    if ids:
        query = (
            scoped_select(CustomFieldOption, ctx)
            .where(CustomFieldOption.definition_id.in_(ids))
            .order_by(CustomFieldOption.position, CustomFieldOption.label, CustomFieldOption.id)
        )
        for option in db.scalars(query):
            grouped.setdefault(option.definition_id, []).append(option)
    return grouped


def definitions_read(
    db: Session, ctx: TenantContext, definitions: Sequence[CustomFieldDefinition]
) -> list[DefinitionRead]:
    options = options_by_definition(
        db, ctx, [d.id for d in definitions if d.field_type == FieldType.SELECT]
    )
    parent_ids = {d.depends_on_definition_id for d in definitions if d.depends_on_definition_id}
    parent_keys: dict[uuid.UUID, str] = {}
    if parent_ids:
        rows = db.execute(
            scoped_select(CustomFieldDefinition, ctx)
            .where(CustomFieldDefinition.id.in_(parent_ids))
            .with_only_columns(CustomFieldDefinition.id, CustomFieldDefinition.key)
        )
        parent_keys = {row.id: row.key for row in rows}
    return [
        DefinitionRead(
            id=d.id,
            entity_type=d.entity_type,
            key=d.key,
            label=d.label,
            field_type=d.field_type,
            required=d.required,
            position=d.position,
            enabled=d.enabled,
            show_in_form=d.show_in_form,
            show_in_table=d.show_in_table,
            show_on_invoice=d.show_on_invoice,
            reference=(
                ReferenceRead(
                    source=d.reference_source,
                    depends_on=parent_keys.get(d.depends_on_definition_id),
                    filter=d.depends_on_filter,
                )
                if d.reference_source
                else None
            ),
            options=(
                [OptionRead.model_validate(o) for o in options.get(d.id, [])]
                if d.field_type == FieldType.SELECT
                else None
            ),
            created_at=d.created_at,
            updated_at=d.updated_at,
        )
        for d in definitions
    ]


def _next_position(db: Session, ctx: TenantContext, entity_type: str) -> int:
    top = db.scalar(
        select(func.coalesce(func.max(CustomFieldDefinition.position), 0)).where(
            CustomFieldDefinition.organization_id == ctx.organization_id,
            CustomFieldDefinition.entity_type == entity_type,
        )
    )
    return top + POSITION_STEP


def _flush_or_409(db: Session, message: str) -> None:
    """Flush; a unique-constraint race becomes a 409 instead of a 500."""
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError as exc:
        if getattr(exc.orig, "sqlstate", None) == UNIQUE_VIOLATION:
            raise HTTPException(status.HTTP_409_CONFLICT, detail=message)
        raise


def _resolve_reference_config(
    db: Session, ctx: TenantContext, entity_type: str, config: ReferenceConfig
) -> tuple[str, uuid.UUID | None, str | None]:
    """Validate a reference field's source and dependency against the registry."""
    source = registry.get(config.source)
    if source is None or source.reference is None:
        raise error(422, ("reference", "source"), "Unknown reference source", "custom_field.reference_source")
    if config.depends_on is None:
        return config.source, None, None

    filter_spec = source.reference.filters.get(config.filter)
    if filter_spec is None:
        raise error(422, ("reference", "filter"), "Unknown filter for this source", "custom_field.filter")
    parent = db.scalar(
        scoped_select(CustomFieldDefinition, ctx).where(
            CustomFieldDefinition.entity_type == entity_type,
            CustomFieldDefinition.key == config.depends_on,
        )
    )
    if parent is None or not parent.enabled or parent.field_type != FieldType.REFERENCE:
        raise error(
            422,
            ("reference", "depends_on"),
            "depends_on must name an enabled reference field of the same entity type",
            "custom_field.depends_on",
        )
    if parent.reference_source != filter_spec.references:
        raise error(
            422,
            ("reference", "depends_on"),
            "That field does not point at the kind of record this filter expects",
            "custom_field.depends_on_type",
        )
    return config.source, parent.id, config.filter


def create_definition(
    db: Session, ctx: TenantContext, payload: DefinitionCreate
) -> CustomFieldDefinition:
    entity = registry.get(payload.entity_type)
    if entity is None or not entity.custom_fields:
        raise error(422, ("entity_type",), "Unknown entity type", "custom_field.entity_type")
    exists = db.scalar(
        select(func.count())
        .select_from(CustomFieldDefinition)
        .where(
            CustomFieldDefinition.organization_id == ctx.organization_id,
            CustomFieldDefinition.entity_type == payload.entity_type,
            CustomFieldDefinition.key == payload.key,
        )
    )
    if exists:
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="A field with this key already exists for this entity type"
        )

    source = parent_id = filter_key = None
    if payload.reference is not None:
        source, parent_id, filter_key = _resolve_reference_config(
            db, ctx, payload.entity_type, payload.reference
        )
    labels = [o.label.casefold() for o in payload.options or []]
    if len(labels) != len(set(labels)):
        raise error(422, ("options",), "Option labels must be unique", "custom_field.option_label")

    definition = create_scoped(
        db,
        ctx,
        CustomFieldDefinition,
        entity_type=payload.entity_type,
        key=payload.key,
        label=payload.label,
        field_type=payload.field_type,
        required=payload.required,
        position=payload.position
        if payload.position is not None
        else _next_position(db, ctx, payload.entity_type),
        show_in_form=payload.show_in_form,
        show_in_table=payload.show_in_table,
        show_on_invoice=payload.show_on_invoice,
        reference_source=source,
        depends_on_definition_id=parent_id,
        depends_on_filter=filter_key,
    )
    for index, option in enumerate(payload.options or [], start=1):
        create_scoped(
            db,
            ctx,
            CustomFieldOption,
            definition_id=definition.id,
            label=option.label,
            position=index * POSITION_STEP,
        )
    _flush_or_409(db, "A field with this key already exists for this entity type")
    return definition


def check_definition_update(db: Session, ctx: TenantContext, definition: CustomFieldDefinition, values: dict) -> None:
    """Rules for enabling/disabling that keep dependency chains consistent."""
    if values.get("enabled") is False and definition.enabled:
        dependents = db.scalar(
            select(func.count())
            .select_from(CustomFieldDefinition)
            .where(
                CustomFieldDefinition.organization_id == ctx.organization_id,
                CustomFieldDefinition.depends_on_definition_id == definition.id,
                CustomFieldDefinition.enabled.is_(True),
            )
        )
        if dependents:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail="Other enabled fields depend on this one; disable them first",
            )
    if values.get("enabled") is True and not definition.enabled and definition.depends_on_definition_id:
        parent = get_scoped(db, ctx, CustomFieldDefinition, definition.depends_on_definition_id)
        if parent is None or not parent.enabled:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail="The field this one depends on is disabled; enable it first",
            )


def _label_taken(options: Iterable[CustomFieldOption], label: str, *, ignore: uuid.UUID | None = None) -> bool:
    return any(o.id != ignore and o.label.casefold() == label.casefold() for o in options)


def add_option(
    db: Session, ctx: TenantContext, definition: CustomFieldDefinition, payload: OptionCreate
) -> CustomFieldOption:
    if definition.field_type != FieldType.SELECT:
        raise error(422, ("label",), "Only select fields have options", "custom_field.not_select")
    existing = options_by_definition(db, ctx, [definition.id]).get(definition.id, [])
    if _label_taken(existing, payload.label):
        raise HTTPException(status.HTTP_409_CONFLICT, detail="This field already has an option with that label")
    position = (max((o.position for o in existing), default=0)) + POSITION_STEP
    option = create_scoped(
        db, ctx, CustomFieldOption, definition_id=definition.id, label=payload.label, position=position
    )
    return option


def check_option_update(
    db: Session,
    ctx: TenantContext,
    definition: CustomFieldDefinition,
    option: CustomFieldOption,
    payload: OptionUpdate,
) -> None:
    if payload.label is not None:
        existing = options_by_definition(db, ctx, [definition.id]).get(definition.id, [])
        if _label_taken(existing, payload.label, ignore=option.id):
            raise HTTPException(status.HTTP_409_CONFLICT, detail="This field already has an option with that label")


# --- choices (what a form offers for a select or reference field) ----------------------------------


def choices(
    db: Session,
    ctx: TenantContext,
    definition: CustomFieldDefinition,
    *,
    q: str | None,
    depends_on_value: uuid.UUID | None,
    include_inactive: bool,
    limit: int,
) -> list[ChoiceRead]:
    if not definition.enabled:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="This field is disabled")

    if definition.field_type == FieldType.SELECT:
        options = options_by_definition(db, ctx, [definition.id]).get(definition.id, [])
        rows = [
            ChoiceRead(id=o.id, label=o.label, active=o.enabled)
            for o in options
            if (include_inactive or o.enabled) and (not q or q.casefold() in o.label.casefold())
        ]
        return rows[:limit]

    if definition.field_type != FieldType.REFERENCE:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="This field has no choices")
    source = registry.get(definition.reference_source or "")
    if source is None or source.reference is None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="The source of this field is unavailable")
    spec = source.reference
    model = source.model

    query = scoped_select(model, ctx)
    if definition.depends_on_definition_id is not None:
        if depends_on_value is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=[
                    {
                        "loc": ["query", "depends_on_value"],
                        "msg": "This field depends on another field; send its current value",
                        "type": "custom_field.depends_on_value",
                    }
                ],
            )
        filter_spec = spec.filters.get(definition.depends_on_filter or "")
        if filter_spec is None:
            raise HTTPException(status.HTTP_409_CONFLICT, detail="The filter of this field is unavailable")
        # An id from another organization simply matches nothing here.
        query = query.where(getattr(model, filter_spec.column) == depends_on_value)
    if spec.active_column and not include_inactive:
        query = query.where(getattr(model, spec.active_column).is_(True))
    if q:
        pattern = contains_pattern(q)
        query = query.where(or_(*[getattr(model, c).ilike(pattern, escape="\\") for c in spec.search_columns]))
    label_column = getattr(model, spec.label_column)
    query = query.order_by(label_column, model.id).limit(limit)
    return [
        ChoiceRead(
            id=record.id,
            label=getattr(record, spec.label_column),
            active=True if not spec.active_column else bool(getattr(record, spec.active_column)),
        )
        for record in db.scalars(query)
    ]


# --- parsing values --------------------------------------------------------------------------------------


def parse_value(definition: CustomFieldDefinition, raw: Any) -> Any:
    """Strictly parse one request value into its stored type. Raises ValueError(message)."""
    kind = definition.field_type
    if kind == FieldType.TEXT:
        if not isinstance(raw, str) or not (1 <= len(raw.strip()) <= TEXT_MAX_LENGTH):
            raise ValueError(f"must be text of 1 to {TEXT_MAX_LENGTH} characters")
        return raw.strip()
    if kind == FieldType.NUMBER:
        if isinstance(raw, bool) or isinstance(raw, float):
            raise ValueError(NUMBER_HINT)
        if isinstance(raw, int):
            raw = str(raw)
        if not isinstance(raw, str) or not NUMBER_RE.fullmatch(raw):
            raise ValueError("must be a decimal with up to 14 digits and 4 decimals; " + NUMBER_HINT)
        return Decimal(raw)
    if kind == FieldType.DATE:
        if not isinstance(raw, str) or not DATE_RE.fullmatch(raw):
            raise ValueError("must be a date written YYYY-MM-DD")
        try:
            return date.fromisoformat(raw)
        except ValueError:
            raise ValueError("must be a real calendar date written YYYY-MM-DD")
    if kind == FieldType.BOOLEAN:
        if not isinstance(raw, bool):
            raise ValueError("must be true or false")
        return raw
    # select and reference both store a UUID
    if not isinstance(raw, str):
        raise ValueError("must be an id")
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise ValueError("must be an id")


def stored_value(row: CustomFieldValue | None) -> Any:
    if row is None:
        return None
    return getattr(row, VALUE_COLUMN[FieldType(row.field_type)])


# --- reading values -----------------------------------------------------------------------------------------


def _number_text(value: Decimal) -> str:
    return format(value.normalize(), "f")


def read_values(
    db: Session,
    ctx: TenantContext,
    entity_type: str,
    entity_ids: Sequence[uuid.UUID],
    *,
    include_disabled: bool = False,
    flag: str | None = None,
) -> dict[uuid.UUID, list[ValueRead]]:
    """Set values of the given entities, in field order, with live display text.

    Callers must pass ids already confirmed to belong to the active organization. `flag` keeps
    only the values of definitions with that property (see load_definitions).
    """
    result: dict[uuid.UUID, list[ValueRead]] = {i: [] for i in entity_ids}
    if not entity_ids:
        return result
    definitions = {
        d.id: d
        for d in load_definitions(db, ctx, entity_type, include_disabled=include_disabled, flag=flag)
    }
    if not definitions:
        return result
    rows = list(
        db.scalars(
            scoped_select(CustomFieldValue, ctx).where(
                CustomFieldValue.entity_type == entity_type,
                CustomFieldValue.entity_id.in_(list(entity_ids)),
                CustomFieldValue.definition_id.in_(list(definitions)),
            )
        )
    )

    option_ids = {r.value_option_id for r in rows if r.value_option_id}
    options = (
        {o.id: o for o in db.scalars(scoped_select(CustomFieldOption, ctx).where(CustomFieldOption.id.in_(option_ids)))}
        if option_ids
        else {}
    )
    # reference targets, grouped by source: {source key: {id: (label, active)}}
    wanted: dict[str, set[uuid.UUID]] = {}
    for r in rows:
        if r.value_reference_id:
            wanted.setdefault(definitions[r.definition_id].reference_source or "", set()).add(r.value_reference_id)
    targets: dict[str, dict[uuid.UUID, tuple[str, bool]]] = {}
    for source_key, ids in wanted.items():
        source = registry.get(source_key)
        if source is None or source.reference is None:
            continue
        spec = source.reference
        columns = [source.model.id, getattr(source.model, spec.label_column)]
        if spec.active_column:
            columns.append(getattr(source.model, spec.active_column))
        found = db.execute(
            select(*columns).where(
                source.model.organization_id == ctx.organization_id, source.model.id.in_(ids)
            )
        )
        targets[source_key] = {
            row[0]: (row[1], bool(row[2]) if spec.active_column else True) for row in found
        }

    by_entity: dict[uuid.UUID, dict[uuid.UUID, CustomFieldValue]] = {}
    for r in rows:
        by_entity.setdefault(r.entity_id, {})[r.definition_id] = r
    ordered = sorted(definitions.values(), key=lambda d: (d.position, d.key))
    for entity_id in entity_ids:
        present = by_entity.get(entity_id, {})
        for d in ordered:
            row = present.get(d.id)
            if row is None:
                continue
            result[entity_id].append(_value_read(d, row, options, targets))
    return result


def _value_read(
    d: CustomFieldDefinition,
    row: CustomFieldValue,
    options: dict[uuid.UUID, CustomFieldOption],
    targets: dict[str, dict[uuid.UUID, tuple[str, bool]]],
) -> ValueRead:
    kind = FieldType(d.field_type)
    base = dict(
        key=d.key, label=d.label, field_type=kind, position=d.position, definition_id=d.id, active=None, missing=False
    )
    if kind == FieldType.TEXT:
        return ValueRead(**base, value=row.value_text, display=row.value_text)
    if kind == FieldType.NUMBER:
        text = _number_text(row.value_number)
        return ValueRead(**base, value=text, display=text)
    if kind == FieldType.DATE:
        text = row.value_date.isoformat()
        return ValueRead(**base, value=text, display=text)
    if kind == FieldType.BOOLEAN:
        return ValueRead(**base, value=row.value_boolean, display="true" if row.value_boolean else "false")
    if kind == FieldType.SELECT:
        option = options.get(row.value_option_id)
        base.update(active=option.enabled if option else None, missing=option is None)
        return ValueRead(**base, value=str(row.value_option_id), display=option.label if option else None)
    found = targets.get(d.reference_source or "", {}).get(row.value_reference_id)
    base.update(active=found[1] if found else None, missing=found is None)
    return ValueRead(**base, value=str(row.value_reference_id), display=found[0] if found else None)


def missing_required(db: Session, ctx: TenantContext, entity_type: str, entity_id: uuid.UUID) -> list[str]:
    required = [
        d
        for d in load_definitions(db, ctx, entity_type, include_disabled=False)
        if d.required
    ]
    if not required:
        return []
    present = set(
        db.scalars(
            scoped_select(CustomFieldValue, ctx)
            .where(CustomFieldValue.entity_id == entity_id, CustomFieldValue.entity_type == entity_type)
            .with_only_columns(CustomFieldValue.definition_id)
        )
    )
    return [d.key for d in sorted(required, key=lambda d: (d.position, d.key)) if d.id not in present]


# --- writing values ---------------------------------------------------------------------------------------------


def write_values(
    db: Session,
    ctx: TenantContext,
    entity_type: str,
    entity_id: uuid.UUID,
    changes: dict[str, Any],
) -> None:
    """Apply `changes` ({key: value or None}) to one record and commit.

    Order matters for correctness under concurrency: the record is looked up in the active
    organization, then `is_editable` runs (which, for locking owners, takes the row lock that
    also guards their lifecycle steps), and only then is anything validated or written. Every
    validation therefore sees committed state of whoever held the lock before us.
    """
    entity = custom_field_entity(entity_type)
    record = get_scoped(db, ctx, entity.model, entity_id)
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")
    if entity.is_editable is not None and not entity.is_editable(db, ctx, entity_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="This record is locked; its custom fields cannot be changed"
        )

    definitions = load_definitions(db, ctx, entity_type)
    by_key = {d.key: d for d in definitions}
    by_id = {d.id: d for d in definitions}
    rows = {
        r.definition_id: r
        for r in db.scalars(
            scoped_select(CustomFieldValue, ctx).where(
                CustomFieldValue.entity_type == entity_type, CustomFieldValue.entity_id == entity_id
            )
        )
    }
    current = {def_id: stored_value(row) for def_id, row in rows.items()}

    errors: list[dict[str, Any]] = []
    changed: dict[uuid.UUID, Any] = {}  # definition id -> new value (None clears)
    for key, raw in changes.items():
        definition = by_key.get(key)
        if definition is None:
            errors.append(err(("values", key), "Unknown field", "custom_field.unknown"))
            continue
        if not definition.enabled:
            errors.append(err(("values", key), "This field is disabled", "custom_field.disabled"))
            continue
        if raw is None:
            new = None
        else:
            try:
                new = parse_value(definition, raw)
            except ValueError as exc:
                errors.append(err(("values", key), str(exc), "custom_field.invalid"))
                continue
        if new != current.get(definition.id):
            changed[definition.id] = new
    if errors:
        raise unprocessable(errors)

    # Existence/activity checks only for values that are NEW or CHANGED: an existing link
    # to something since deactivated stays valid.
    for definition_id, new in changed.items():
        if new is None:
            continue
        definition = by_id[definition_id]
        problem = _check_target(db, ctx, definition, new)
        if problem:
            errors.append(err(("values", definition.key), *problem))
    if errors:
        raise unprocessable(errors)

    final = {**current}
    for definition_id, new in changed.items():
        if new is None:
            final.pop(definition_id, None)
        else:
            final[definition_id] = new

    for definition in definitions:
        if not definition.enabled or definition.depends_on_definition_id is None:
            continue
        parent = by_id.get(definition.depends_on_definition_id)
        if parent is None or (definition.id not in changed and parent.id not in changed):
            continue
        child_value = final.get(definition.id)
        if child_value is None:
            continue
        parent_value = final.get(parent.id)
        if parent_value is None:
            errors.append(err(("values", definition.key), f"Set {parent.label} first", "custom_field.dependency"))
        elif not _satisfies_filter(db, ctx, definition, child_value, parent_value):
            errors.append(
                err(
                    ("values", definition.key),
                    f"This is not one of the choices for the selected {parent.label}",
                    "custom_field.dependency",
                )
            )
    for definition in definitions:
        if definition.enabled and definition.required and final.get(definition.id) is None:
            errors.append(err(("values", definition.key), f"{definition.label} is required", "custom_field.required"))
    if errors:
        raise unprocessable(errors)

    shown_before = _shown(db, ctx, entity_type, entity_id)
    for definition_id, new in changed.items():
        row = rows.get(definition_id)
        definition = by_id[definition_id]
        column = VALUE_COLUMN[FieldType(definition.field_type)]
        if new is None:
            if row is not None:
                db.delete(row)
        elif row is not None:
            setattr(row, column, new)
        else:
            create_scoped(
                db,
                ctx,
                CustomFieldValue,
                definition_id=definition_id,
                entity_type=entity_type,
                field_type=definition.field_type,
                entity_id=entity_id,
                **{column: new},
            )
    _flush_or_409(db, "These custom fields were changed at the same time; try again")
    _record_history(db, ctx, entity, record, shown_before, _shown(db, ctx, entity_type, entity_id))
    db.commit()


def _shown(db: Session, ctx: TenantContext, entity_type: str, entity_id: uuid.UUID) -> dict[str, tuple[str, str | None]]:
    """key -> (label, display text) of the record's values, as a person reads them."""
    values = read_values(db, ctx, entity_type, [entity_id], include_disabled=True)[entity_id]
    return {v.key: (v.label, v.display if v.display is not None else None if v.value is None else str(v.value)) for v in values}


def _record_history(db: Session, ctx: TenantContext, entity, record, before, after) -> None:
    """One history event on the record that owns the values, in the words a person saw (labels and display
    text, not option or record ids). A record that belongs to a parent (a line of a transaction) names it as
    context, so the parent's history shows it too."""
    changes = {
        key: {"label": (after.get(key) or before.get(key))[0], "from": before.get(key, (None, None))[1], "to": after.get(key, (None, None))[1]}
        for key in sorted(set(before) | set(after))
        if before.get(key, (None, None))[1] != after.get(key, (None, None))[1]
    }
    if not changes:
        return
    context = (entity.parent.entity, getattr(record, entity.parent.column)) if entity.parent is not None else None
    if hasattr(record, "updated_by"):
        audit.stamp(record, ctx)
    audit.record(db, ctx, entity_type=entity.key, entity_id=record.id, action="fields_updated", changes=changes, context=context)


def _check_target(db: Session, ctx: TenantContext, definition: CustomFieldDefinition, value: Any) -> tuple[str, str] | None:
    """(message, error type) if a new select/reference value points at nothing selectable."""
    if definition.field_type == FieldType.SELECT:
        option = db.scalar(
            scoped_select(CustomFieldOption, ctx).where(
                CustomFieldOption.definition_id == definition.id, CustomFieldOption.id == value
            )
        )
        if option is None:
            return "Option not found", "custom_field.option_not_found"
        if not option.enabled:
            return "This option is disabled", "custom_field.option_disabled"
        return None
    if definition.field_type == FieldType.REFERENCE:
        source = registry.get(definition.reference_source or "")
        if source is None or source.reference is None:
            return "The source of this field is unavailable", "custom_field.reference_source"
        record = get_scoped(db, ctx, source.model, value)  # identical for foreign and missing ids
        if record is None:
            return f"{source.label} not found", "reference.not_found"
        active_column = source.reference.active_column
        if active_column and not getattr(record, active_column):
            return f"{source.label} is inactive", "reference.inactive"
    return None


def _satisfies_filter(
    db: Session, ctx: TenantContext, definition: CustomFieldDefinition, child_value: uuid.UUID, parent_value: uuid.UUID
) -> bool:
    source = registry.get(definition.reference_source or "")
    if source is None or source.reference is None:
        return False
    filter_spec = source.reference.filters.get(definition.depends_on_filter or "")
    if filter_spec is None:
        return False
    model = source.model
    return (
        db.scalar(
            scoped_select(model, ctx)
            .where(model.id == child_value, getattr(model, filter_spec.column) == parent_value)
            .with_only_columns(model.id)
        )
        is not None
    )


# --- generic hooks registered on the core registry ----------------------------------------------------------------


def _collect_entities(
    db: Session, ctx: TenantContext, entity_key: str, entity_ids: list[uuid.UUID]
) -> dict[str, list[uuid.UUID]]:
    """The given records plus everything below them, per registered child entity type."""
    collected: dict[str, list[uuid.UUID]] = {entity_key: list(entity_ids)}
    for child in registry.children_of(entity_key):
        parent_column = getattr(child.model, child.parent.column)
        child_ids = list(
            db.scalars(
                select(child.model.id)
                .where(child.model.organization_id == ctx.organization_id, parent_column.in_(entity_ids))
                .order_by(child.model.id)
            )
        )
        if child_ids:
            for key, ids in _collect_entities(db, ctx, child.key, child_ids).items():
                collected.setdefault(key, []).extend(ids)
    return collected


def required_fields_validator(
    db: Session, ctx: TenantContext, event: str, entity_key: str, entity_id: uuid.UUID
) -> list[Problem]:
    """Block the "complete" step while an enabled required field has no value on the record
    or on any record below it. Only looks at the active organization."""
    if event != COMPLETE:
        return []
    problems: list[Problem] = []
    for entity_type, ids in _collect_entities(db, ctx, entity_key, [entity_id]).items():
        entity = registry.get(entity_type)
        if entity is None or not entity.custom_fields:
            continue
        required = [
            d for d in load_definitions(db, ctx, entity_type, include_disabled=False) if d.required
        ]
        if not required:
            continue
        present = set(
            tuple(row) for row in db.execute(
                scoped_select(CustomFieldValue, ctx)
                .where(
                    CustomFieldValue.entity_type == entity_type,
                    CustomFieldValue.entity_id.in_(ids),
                    CustomFieldValue.definition_id.in_([d.id for d in required]),
                )
                .with_only_columns(CustomFieldValue.entity_id, CustomFieldValue.definition_id)
            )
        )
        for record_id in ids:
            for d in sorted(required, key=lambda d: (d.position, d.key)):
                if (record_id, d.id) not in present:
                    problems.append(
                        Problem(
                            code="custom_field.required",
                            message=f"{d.label} is required",
                            entity_type=entity_type,
                            entity_id=str(record_id),
                            field=d.key,
                            label=d.label,
                        )
                    )
    return problems


def referenced_by_live_values(
    db: Session, entity_key: str, organization_id: uuid.UUID, record_id: uuid.UUID
) -> bool:
    """Reference guard: does a custom value of a still-existing record point at this one?

    Values left behind by deleted records do not count, so they can never block a delete.
    """
    for entity in registry.custom_field_types():
        query = (
            select(CustomFieldValue.id)
            .join(
                CustomFieldDefinition,
                and_(
                    CustomFieldDefinition.organization_id == CustomFieldValue.organization_id,
                    CustomFieldDefinition.id == CustomFieldValue.definition_id,
                ),
            )
            .join(
                entity.model,
                and_(
                    entity.model.organization_id == CustomFieldValue.organization_id,
                    entity.model.id == CustomFieldValue.entity_id,
                ),
            )
            .where(
                CustomFieldValue.organization_id == organization_id,
                CustomFieldValue.entity_type == entity.key,
                CustomFieldValue.value_reference_id == record_id,
                CustomFieldDefinition.reference_source == entity_key,
            )
            .limit(1)
        )
        if db.scalar(query) is not None:
            return True
    return False
