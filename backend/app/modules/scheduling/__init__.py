"""Scheduling: appointments in a calendar, per practitioner and for the whole organization.

A generic module: it knows customers and members, and meets any other record (a horse) only as a registered "subject" through the
core registry. Nothing outside this package (except main.py, the dev seed and Alembic's env.py) may import it.
"""

from app.modules.scheduling.api import router
from app.modules.scheduling.registration import register

__all__ = ["register", "router"]
