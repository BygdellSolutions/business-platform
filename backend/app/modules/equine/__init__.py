"""Equine: an optional domain module (horses).

Dependency direction: this module may import core and customers. Nothing outside
this package (except main.py, the dev seed and Alembic's env.py) may import it or
mention horses; tests/test_module_boundaries.py enforces that.
"""

from app.modules.equine.api import router

__all__ = ["router"]
