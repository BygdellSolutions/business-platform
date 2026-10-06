from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """The declarative base of every model. It lives apart from `app.core.db` so that importing the models (Alembic does)
    never builds the runtime engine or reads the web process's settings: the migration job has its own credentials."""
