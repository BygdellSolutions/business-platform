import type { ApiError, FieldErrors, Problem } from "@/lib/api/errors";
import type { TransactionLine } from "@/lib/api/types";

/**
 * What a failed Sales request MEANS for the editor. The backend decides everything; this only
 * sorts its answers into the few cases the screen reacts to differently.
 *
 *   validation  422: show messages on the controls that caused them
 *   stale       409 `stale_record`: the change was based on an old version. Nothing was changed
 *               on the server. The editor keeps the user's draft and lets them choose.
 *   problems    409 `validation_failed`: a lifecycle step was blocked, with a list of problems
 *   conflict    any other 409: the transaction is not in a state that allows this (it is no
 *               longer a draft, it has no lines, ...). The message is the backend's own.
 *   gone        404: the record is not there (deleted, or never in this organization)
 *   other       403, network, server, 428/400, ...: the established generic messages
 */
export type Failure =
  | { kind: "validation"; fieldErrors: FieldErrors; formErrors: string[] }
  | { kind: "stale"; message: string; currentVersion?: number }
  | { kind: "problems"; message: string; problems: Problem[] }
  | { kind: "conflict"; message: string }
  | { kind: "gone" }
  | { kind: "other"; message: string };

export function classify(error: ApiError): Failure {
  switch (error.kind) {
    case "validation":
      return { kind: "validation", fieldErrors: error.fieldErrors, formErrors: error.formErrors };
    case "conflict":
      if (error.code === "stale_record") return { kind: "stale", message: error.message, currentVersion: error.currentVersion };
      if (error.code === "validation_failed") return { kind: "problems", message: error.message, problems: error.problems };
      return { kind: "conflict", message: error.message };
    case "not_found":
      return { kind: "gone" };
    default:
      return { kind: "other", message: error.message };
  }
}

/** Plain-language texts for the situations the editor explains, in one place. */
export const TEXT = {
  notDraft: "This order is no longer a draft, so your changes could not be saved. The latest version is shown.",
  staleElsewhere: "This order was changed elsewhere, so nothing was changed. The latest version is shown.",
  gone: "This record no longer exists, or you do not have access to it. The latest version is shown.",
  lineChanged: "This line was changed elsewhere. Your edits were not saved.",
  headerChanged: "The header was changed elsewhere. Your edits were not saved.",
} as const;

/** One line of the completion-problems list: "Line 2 · Owner: is required", "Order · ...". */
export function describeProblem(problem: Problem, lines: Pick<TransactionLine, "id">[]): string {
  const index = problem.entity_type === "transaction_line" ? lines.findIndex((line) => line.id === problem.entity_id) : -1;
  const where = index >= 0 ? `Line ${index + 1}` : problem.entity_type === "transaction" ? "Order" : "Record";
  const what = problem.label ?? problem.field;
  return `${where}${what ? ` · ${what}` : ""}: ${problem.message}`;
}
