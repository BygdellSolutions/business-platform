# Import every model here so Alembic autogenerate sees it on Base.metadata.
from app.models.organization import Organization

__all__ = ["Organization"]
