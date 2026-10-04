import type { Member, Role } from "@/lib/api/types";

/**
 * What membership administration OFFERS, from the roles the screen currently knows. This is presentation only: it
 * mirrors the approved matrix so the page does not show buttons that are certain to be refused, and FastAPI
 * re-decides every request from fresh rows (a stale screen is answered with a refusal, which the UI shows). The
 * last-owner rule is deliberately NOT calculated here beyond hiding a self-demotion that cannot be legal.
 *
 *   owner   any role for another member, removal of another member; may step down themselves (not the last owner)
 *   admin   only members who are accountants, employees or viewers, and only to those roles; removal likewise
 *   others  nothing
 */
export const ALL_ROLES: readonly Role[] = ["owner", "admin", "accountant", "employee", "viewer"];
export const ADMIN_MANAGEABLE: readonly Role[] = ["accountant", "employee", "viewer"];

export const ROLE_LABELS: Record<Role, string> = { owner: "Owner", admin: "Admin", accountant: "Accountant", employee: "Employee", viewer: "Viewer" };

export function canAdminister(role: Role | undefined): boolean {
  return role === "owner" || role === "admin";
}

export interface Offered {
  /** Roles the member could be changed to (empty: no role control). */
  roles: readonly Role[];
  remove: boolean;
}

export function offered(actorRole: Role, member: Member, ownersListed: number): Offered {
  if (member.is_you) {
    // Only an owner may change their own role, and only to step down; leaving is a separate control.
    return { roles: actorRole === "owner" && ownersListed > 1 ? ALL_ROLES.filter((role) => role !== "owner") : [], remove: false };
  }
  if (actorRole === "owner") return { roles: ALL_ROLES, remove: true };
  if (actorRole === "admin" && ADMIN_MANAGEABLE.includes(member.role)) return { roles: ADMIN_MANAGEABLE, remove: true };
  return { roles: [], remove: false };
}
