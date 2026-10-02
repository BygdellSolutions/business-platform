"""Sales: industry-neutral transactions (header + lines).

Dependency direction: this module may import core, Customers and Catalog. It must
never import or mention a domain (industry-specific) module: that context is attached
later through the generic custom-field/reference mechanism. Nothing but main.py, the
dev seed and Alembic's env.py may import this package; tests/test_module_boundaries.py
enforces both directions.
"""

from app.modules.sales.api import router

__all__ = ["router"]
