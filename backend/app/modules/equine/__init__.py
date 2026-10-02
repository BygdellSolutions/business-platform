"""Equine: an optional domain module (horses).

Dependency direction: this module may import core and customers. Nothing outside
this package (except main.py, the dev seed and Alembic's env.py) may import it, and it
meets other modules only through the core registry (see registration.py);
tests/test_module_boundaries.py enforces the import rules.
"""

from app.modules.equine.api import router
from app.modules.equine.registration import register

__all__ = ["register", "router"]
