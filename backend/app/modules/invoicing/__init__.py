"""Invoicing: invoices built from whole completed Sales transactions.

Dependency direction: this module may import core, Customers, Sales and Custom Fields. None of
them imports it. Sales learns nothing about invoices: an invoice's reservation of a transaction
is a row in this module's own tables, and it blocks Sales' reopen and cancel only through the
core lifecycle seam (see registration.py). Nothing but main.py (mounts the routers and registers
the validator), the Alembic env and the dev seed may import this package;
tests/test_module_boundaries.py enforces the rules in both directions.
"""

from app.modules.invoicing.api import invoiceable_router, router
from app.modules.invoicing.registration import register

__all__ = ["invoiceable_router", "register", "router"]
