"""Small authorization helpers. Roles come from the ACTIVE membership only.

The role in `TenantContext` is the user's role in the organization the request is
scoped to (never a role they hold elsewhere), so one person can be an owner in one
organization and a viewer in another and always gets the right answer.
"""

from collections.abc import Callable, Iterable

from fastapi import Depends, HTTPException, status

from app.core.tenant import TenantContext, get_tenant_context
from app.models.organization_user import Role

# Who may create, change and delete an organization's business records (customers, items,
# domain records, transactions and their custom values). A viewer reads only. Narrower rules
# (invoicing, settings, custom-field definitions) declare their own role sets.
RECORD_WRITERS = frozenset({Role.OWNER, Role.ADMIN, Role.ACCOUNTANT, Role.EMPLOYEE})


def require_role(ctx: TenantContext, allowed_roles: Iterable[Role]) -> None:
    """Raise 403 unless the user's role in the active organization is allowed."""
    if ctx.role not in set(allowed_roles):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            detail="Your role in this organization does not allow this action",
        )


def roles_required(*allowed_roles: Role) -> Callable[..., TenantContext]:
    """FastAPI dependency: the tenant context, but only for the allowed roles.

    Tenant resolution runs first, so selecting an organization you do not belong to is
    still a 404, never a 403.
    """

    def dependency(ctx: TenantContext = Depends(get_tenant_context)) -> TenantContext:
        require_role(ctx, allowed_roles)
        return ctx

    # Lets a test walk the routes and prove which roles each write endpoint admits.
    dependency.allowed_roles = frozenset(allowed_roles)  # type: ignore[attr-defined]
    return dependency


# FastAPI dependency for every write to business records: the tenant context of a record writer.
record_writer = roles_required(*RECORD_WRITERS)
