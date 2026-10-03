import type { ApiError, FieldErrors } from "@/lib/api/errors";

/**
 * What a failed Invoicing request MEANS for the screen. The backend decides everything; this
 * only sorts its answers into the few cases the screens react to differently.
 *
 *   validation   422: show the messages on the controls that caused them
 *   stale        409 `stale_record`: the change was based on an old version; nothing changed
 *   issued       409 `invoice_issued`: the invoice is no longer a draft (issued elsewhere)
 *   sourceChanged 409 `source_changed`: the draft no longer matches its source transactions, so
 *                it cannot be issued (delete it and create another)
 *   ineligible   409 about the SOURCE transactions of a new invoice (no longer completed, already
 *                invoiced, mixed customers or currencies, no currency), naming the ones concerned
 *   conflict     any other 409: the backend's own message
 *   gone         404: not there (deleted, or never in this organization)
 *   unconfirmed  the outcome is UNKNOWN: the network failed, the server errored or timed out. The
 *                request may or may not have been applied, so it must be checked, never assumed
 *   other        403, 400/428, ...: the established generic messages
 */
export type InvoiceFailure =
  | { kind: "validation"; fieldErrors: FieldErrors; formErrors: string[] }
  | { kind: "stale"; message: string; currentVersion?: number }
  | { kind: "issued"; message: string }
  | { kind: "sourceChanged"; message: string }
  | { kind: "ineligible"; code: string; message: string; transactionIds: string[] }
  | { kind: "conflict"; message: string }
  | { kind: "gone" }
  | { kind: "unconfirmed"; message: string }
  | { kind: "other"; message: string };

const INELIGIBLE_CODES = ["transactions_not_completed", "mixed_customers", "currency_missing", "mixed_currencies", "already_invoiced"];

export function classify(error: ApiError): InvoiceFailure {
  switch (error.kind) {
    case "validation":
      return { kind: "validation", fieldErrors: error.fieldErrors, formErrors: error.formErrors };
    case "conflict":
      if (error.code === "stale_record") return { kind: "stale", message: error.message, currentVersion: error.currentVersion };
      if (error.code === "invoice_issued") return { kind: "issued", message: error.message };
      if (error.code === "source_changed") return { kind: "sourceChanged", message: error.message };
      if (error.code && INELIGIBLE_CODES.includes(error.code)) return { kind: "ineligible", code: error.code, message: error.message, transactionIds: error.transactionIds };
      return { kind: "conflict", message: error.message };
    case "not_found":
      return { kind: "gone" };
    case "network":
    case "server":
      return { kind: "unconfirmed", message: error.message };
    default:
      return { kind: "other", message: error.message };
  }
}

/** Plain-language texts for the situations the screens explain, in one place. */
export const TEXT = {
  staleElsewhere: "This invoice was changed elsewhere, so nothing was changed. The latest version is shown.",
  issuedElsewhere: "This invoice is no longer a draft, so your change could not be saved. The latest version is shown.",
  gone: "This invoice no longer exists, or you do not have access to it.",
  unconfirmed: "We could not confirm whether that worked. The latest state is being checked; nothing is assumed.",
  verified: "Checked: the invoice is as shown below.",
  headerChanged: "The invoice was changed elsewhere. Your edits were not saved.",
  sourceChanged: "A source transaction no longer matches this draft, so it cannot be issued. Delete the draft and create a new invoice.",
} as const;
