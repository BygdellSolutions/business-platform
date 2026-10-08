"""Inventory: an optional module that keeps a stock ledger for the products that track stock.

Dependency direction: this module may import core. Nothing outside this package (except the wiring
files and Alembic's env.py) may import it; Sales never learns about it. It reacts to transaction
lifecycle steps through the effects of the core lifecycle seam (from slice I3).
tests/test_module_boundaries.py enforces the import rules.
"""

from app.modules.inventory.api import router

__all__ = ["router"]
