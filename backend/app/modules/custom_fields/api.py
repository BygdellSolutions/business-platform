import uuid
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.core.authz import roles_required
from app.core.db import get_db
from app.core.entity_registry import registry
from app.core.query import apply_update, commit_and_refresh
from app.core.tenant import TenantContext, get_tenant_context
from app.core.tenant_scope import get_scoped, get_scoped_or_404, scoped_select
from app.models.organization_user import Role
from app.modules.custom_fields import service
from app.modules.custom_fields.models import CustomFieldDefinition, CustomFieldOption
from app.modules.custom_fields.schemas import (
    MAX_BULK_ENTITIES,
    BulkValuesRead,
    ChoiceRead,
    DefinitionCreate,
    DefinitionRead,
    DefinitionUpdate,
    EntityTypeRead,
    EntityValuesRead,
    FilterRead,
    OptionCreate,
    OptionRead,
    OptionUpdate,
    ValuesPatch,
)

router = APIRouter(prefix="/api/custom-fields", tags=["custom-fields"])

# Keep in step with service.FLAGS (a test compares them).
Flag = Literal["required", "show_in_form", "show_in_table", "show_on_invoice"]

# Defining fields reshapes forms for the whole organization: owners and admins only.
# Reading definitions and writing values stays open to every member.
administer = roles_required(Role.OWNER, Role.ADMIN)


# --- what exists (registry metadata for admin screens) ------------------------------------------------


@router.get("/entity-types", response_model=list[EntityTypeRead])
def list_entity_types(ctx: TenantContext = Depends(get_tenant_context)) -> list[EntityTypeRead]:
    return [
        EntityTypeRead(
            key=entity.key,
            label=entity.label,
            custom_fields=entity.custom_fields,
            referenceable=entity.reference is not None,
            filters=[
                FilterRead(key=key, label=spec.label, references=spec.references)
                for key, spec in (entity.reference.filters.items() if entity.reference else [])
            ],
        )
        for entity in registry.all()
    ]


# --- definitions -----------------------------------------------------------------------------------------


@router.get("/definitions", response_model=list[DefinitionRead])
def list_definitions(
    entity_type: str | None = None,
    include_disabled: bool = False,
    flag: Flag | None = None,
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> list[DefinitionRead]:
    definitions = service.load_definitions(
        db, ctx, entity_type, include_disabled=include_disabled, flag=flag, limit=limit, offset=offset
    )
    return service.definitions_read(db, ctx, definitions)


@router.post("/definitions", response_model=DefinitionRead, status_code=status.HTTP_201_CREATED)
def create_definition(
    payload: DefinitionCreate,
    ctx: TenantContext = Depends(administer),
    db: Session = Depends(get_db),
) -> DefinitionRead:
    definition = service.create_definition(db, ctx, payload)
    db.commit()
    return service.definitions_read(db, ctx, [definition])[0]


@router.get("/definitions/{definition_id}", response_model=DefinitionRead)
def read_definition(
    definition_id: uuid.UUID,
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> DefinitionRead:
    definition = get_scoped_or_404(db, ctx, CustomFieldDefinition, definition_id)
    return service.definitions_read(db, ctx, [definition])[0]


@router.patch("/definitions/{definition_id}", response_model=DefinitionRead)
def update_definition(
    definition_id: uuid.UUID,
    payload: DefinitionUpdate,
    ctx: TenantContext = Depends(administer),
    db: Session = Depends(get_db),
) -> DefinitionRead:
    definition = get_scoped_or_404(db, ctx, CustomFieldDefinition, definition_id, for_update=True)
    values = payload.model_dump(exclude_unset=True)
    service.check_definition_update(db, ctx, definition, values)
    apply_update(db, definition, values)
    return service.definitions_read(db, ctx, [definition])[0]


# --- select options ----------------------------------------------------------------------------------------


@router.post(
    "/definitions/{definition_id}/options", response_model=OptionRead, status_code=status.HTTP_201_CREATED
)
def add_option(
    definition_id: uuid.UUID,
    payload: OptionCreate,
    ctx: TenantContext = Depends(administer),
    db: Session = Depends(get_db),
) -> CustomFieldOption:
    definition = get_scoped_or_404(db, ctx, CustomFieldDefinition, definition_id)
    option = service.add_option(db, ctx, definition, payload)
    commit_and_refresh(db, option)
    return option


@router.patch("/definitions/{definition_id}/options/{option_id}", response_model=OptionRead)
def update_option(
    definition_id: uuid.UUID,
    option_id: uuid.UUID,
    payload: OptionUpdate,
    ctx: TenantContext = Depends(administer),
    db: Session = Depends(get_db),
) -> CustomFieldOption:
    definition = get_scoped_or_404(db, ctx, CustomFieldDefinition, definition_id)
    # Both the organization AND the definition in the path must match the option.
    option = db.scalar(
        scoped_select(CustomFieldOption, ctx).where(
            CustomFieldOption.id == option_id, CustomFieldOption.definition_id == definition.id
        )
    )
    if option is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")
    service.check_option_update(db, ctx, definition, option, payload)
    apply_update(db, option, payload.model_dump(exclude_unset=True))
    return option


# --- choices for forms --------------------------------------------------------------------------------------


@router.get("/definitions/{definition_id}/choices", response_model=list[ChoiceRead])
def list_choices(
    definition_id: uuid.UUID,
    q: str | None = Query(default=None, max_length=255),
    depends_on_value: uuid.UUID | None = None,
    include_inactive: bool = False,
    limit: int = Query(default=50, ge=1, le=200),
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> list[ChoiceRead]:
    definition = get_scoped_or_404(db, ctx, CustomFieldDefinition, definition_id)
    return service.choices(
        db,
        ctx,
        definition,
        q=q,
        depends_on_value=depends_on_value,
        include_inactive=include_inactive,
        limit=limit,
    )


# --- values --------------------------------------------------------------------------------------------------


def _confirmed_entity(db: Session, ctx: TenantContext, entity_type: str, entity_id: uuid.UUID) -> None:
    entity = service.custom_field_entity(entity_type)
    if get_scoped(db, ctx, entity.model, entity_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Not found")


def _entity_values(
    db: Session,
    ctx: TenantContext,
    entity_type: str,
    entity_id: uuid.UUID,
    include_disabled: bool,
    flag: str | None = None,
) -> EntityValuesRead:
    values = service.read_values(
        db, ctx, entity_type, [entity_id], include_disabled=include_disabled, flag=flag
    )
    return EntityValuesRead(
        entity_type=entity_type,
        entity_id=entity_id,
        values=values[entity_id],
        missing_required=service.missing_required(db, ctx, entity_type, entity_id),
    )


@router.get("/entities/{entity_type}/{entity_id}/values", response_model=EntityValuesRead)
def read_entity_values(
    entity_type: str,
    entity_id: uuid.UUID,
    include_disabled: bool = False,
    flag: Flag | None = None,
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> EntityValuesRead:
    _confirmed_entity(db, ctx, entity_type, entity_id)
    return _entity_values(db, ctx, entity_type, entity_id, include_disabled, flag)


@router.patch("/entities/{entity_type}/{entity_id}/values", response_model=EntityValuesRead)
def write_entity_values(
    entity_type: str,
    entity_id: uuid.UUID,
    payload: ValuesPatch,
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> EntityValuesRead:
    service.write_values(db, ctx, entity_type, entity_id, payload.values)
    return _entity_values(db, ctx, entity_type, entity_id, include_disabled=False)


@router.get("/values", response_model=BulkValuesRead)
def read_bulk_values(
    entity_type: str,
    entity_ids: str = Query(description="Comma-separated ids"),
    include_disabled: bool = False,
    flag: Flag | None = None,
    ctx: TenantContext = Depends(get_tenant_context),
    db: Session = Depends(get_db),
) -> BulkValuesRead:
    """Values of several records at once (table columns). Ids that are not records of the
    active organization are simply left out; they are indistinguishable from nonexistent."""
    entity = service.custom_field_entity(entity_type)
    try:
        ids = list(dict.fromkeys(uuid.UUID(part) for part in entity_ids.split(",") if part.strip()))
    except ValueError:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=[{"loc": ["query", "entity_ids"], "msg": "must be comma-separated ids", "type": "value_error"}],
        )
    if len(ids) > MAX_BULK_ENTITIES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=[{"loc": ["query", "entity_ids"], "msg": f"at most {MAX_BULK_ENTITIES} ids", "type": "value_error"}],
        )
    owned = set(
        db.scalars(scoped_select(entity.model, ctx).where(entity.model.id.in_(ids)).with_only_columns(entity.model.id))
    )
    confirmed = [i for i in ids if i in owned]
    values = service.read_values(
        db, ctx, entity_type, confirmed, include_disabled=include_disabled, flag=flag
    )
    return BulkValuesRead(entity_type=entity_type, entities=values)
