"""Inventory: an optional module that keeps a stock ledger for the products that track stock.

Dependency direction: this module may import core and READ Sales' models (the lines a transaction asks for;
see tests/test_module_boundaries.py). Nothing outside this package (except the wiring
files and Alembic's env.py) may import it; Sales never learns about it. It reacts to transaction
lifecycle steps through the effects of the core lifecycle seam (from slice I3).
tests/test_module_boundaries.py enforces the import rules.
"""

from app.modules.inventory.api import availability_router, router

__all__ = ["availability_router", "router"]
