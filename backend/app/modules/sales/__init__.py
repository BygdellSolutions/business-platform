"""Sales: industry-neutral transactions (header + lines).

Dependency direction: this module may import core, Customers and Catalog. It never
imports a domain (industry-specific) module or the custom-fields module: that context is
attached through the generic custom-field/reference mechanism, and Sales meets it only
through the core registry (see registration.py and app/core/lifecycle.py). Nothing but
main.py, the dev seed and Alembic's env.py may import this package;
tests/test_module_boundaries.py enforces the import rules in both directions.
"""

from app.modules.sales.api import router
from app.modules.sales.registration import register

__all__ = ["register", "router"]
