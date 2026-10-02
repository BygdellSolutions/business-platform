"""Custom fields (user-defined fields): organization-configurable fields on registered entities.

A GENERIC capability. It depends on core only (the entity registry, tenant scoping,
lifecycle validation, authorization): it never imports a module, and no module imports it.
Modules expose their entities to it by registering them on the core registry.
tests/test_module_boundaries.py enforces the import rules.
"""

from app.modules.custom_fields.api import router
from app.modules.custom_fields.registration import register

__all__ = ["register", "router"]
