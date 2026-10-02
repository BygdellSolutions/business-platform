from app.core.entity_registry import Registry
from app.modules.custom_fields.service import referenced_by_live_values, required_fields_validator


def register(registry: Registry) -> None:
    """Hook into the generic seams other modules already use, without them knowing us:

    * delete_or_409 asks the reference guard before deleting a record, so a record that a
      custom value points at (a link no foreign key can express) cannot be deleted;
    * lifecycle steps such as "complete" ask the validator, so required custom fields must
      be filled before a record is finalized and locked.
    """
    registry.add_reference_guard(referenced_by_live_values)
    registry.add_validator(required_fields_validator)
