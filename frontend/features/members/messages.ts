import type { ApiError } from "@/lib/api/errors";

/**
 * What a refused membership change tells the person. The screen may have been stale (a role changed, a member left,
 * the person's own authority was reduced), so every refusal is worded as "this is no longer possible" and is
 * followed by a refresh from the server; nothing is shown as done that the server did not do.
 */
export function failureMessage(error: ApiError): string {
  switch (error.kind) {
    case "conflict":
      if (error.code === "last_owner") return "An organization must keep at least one owner. Make someone else an owner first.";
      return "That conflicts with the current state of the organization. The list has been refreshed.";
    case "forbidden":
      return "You are not allowed to do that, or the roles involved have changed. The list has been refreshed.";
    case "not_found":
      return "That member no longer exists, or you no longer have access to them. The list has been refreshed.";
    case "validation":
      return "That role is not valid.";
    case "network":
      return "Could not reach the server. Nothing was changed that we know of; the list has been refreshed.";
    default:
      return "Something went wrong. The list has been refreshed.";
  }
}
