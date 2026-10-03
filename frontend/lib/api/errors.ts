/**
 * One place that turns a backend error response into something the UI can act on.
 *
 * FastAPI answers:
 *   401 {"detail": "Not authenticated"}
 *   403 {"detail": "Your role ..."}
 *   404 {"detail": "Not found"}
 *   409 {"detail": "text"}  or  {"detail": {"code": "validation_failed", "problems": [...]}}
 *   422 {"detail": [{"loc": ["body", "values", "owner"], "msg": "...", "type": "..."}]}
 */

export interface Problem {
  code: string;
  message: string;
  entity_type: string;
  entity_id: string;
  field: string | null;
  label: string | null;
}

/** Field errors keyed by dotted path without the leading "body": "name", "values.owner", "lines.1.item_id". */
export type FieldErrors = Record<string, string[]>;

export type ApiError =
  | { kind: "unauthorized"; status: 401; message: string }
  | { kind: "forbidden"; status: 403; message: string }
  | { kind: "not_found"; status: 404; message: string }
  | {
      kind: "conflict";
      status: 409;
      message: string;
      /**
       * Set for the structured conflicts: "validation_failed" (a lifecycle step was blocked,
       * see `problems`) and "stale_record" (the change was based on an old version).
       */
      code?: string;
      problems: Problem[];
      /** For "stale_record": the version the record has now. */
      currentVersion?: number;
      /** For conflicts about transactions (not invoiceable any more): the ids concerned. */
      transactionIds: string[];
    }
  | { kind: "validation"; status: 422; message: string; fieldErrors: FieldErrors; formErrors: string[] }
  | { kind: "client"; status: number; message: string }
  | { kind: "server"; status: number; message: string }
  | { kind: "network"; status: 0; message: string };

export type ApiResult<T> = { ok: true; status: number; data: T } | { ok: false; error: ApiError };

const GENERIC: Record<number, string> = {
  401: "You are not signed in.",
  403: "Your role in this organization does not allow this.",
  404: "Not found.",
  409: "That conflicts with the current state.",
  422: "Some values are not valid.",
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function detailOf(body: unknown): unknown {
  return isRecord(body) ? body.detail : undefined;
}

/** "values.owner" for ["body", "values", "owner"]; null when nothing is left. */
export function fieldPath(loc: unknown): string | null {
  if (!Array.isArray(loc)) return null;
  const parts = loc.map(String);
  if (parts[0] === "body") parts.shift();
  return parts.length > 0 ? parts.join(".") : null;
}

/** Pydantic prefixes messages from custom validators with "Value error, "; users do not need it. */
function cleanMessage(message: string): string {
  return message.replace(/^Value error, /, "");
}

function validationErrors(detail: unknown): { fieldErrors: FieldErrors; formErrors: string[] } {
  const fieldErrors: FieldErrors = {};
  const formErrors: string[] = [];
  const items = Array.isArray(detail) ? detail : [];
  for (const item of items) {
    if (!isRecord(item)) continue;
    const message = typeof item.msg === "string" ? cleanMessage(item.msg) : "Not valid";
    const path = fieldPath(item.loc);
    if (path === null) formErrors.push(message);
    else (fieldErrors[path] ??= []).push(message);
  }
  if (typeof detail === "string") formErrors.push(detail);
  return { fieldErrors, formErrors };
}

function toProblems(value: unknown): Problem[] {
  if (!Array.isArray(value)) return [];
  return value.filter(isRecord).map((p) => ({
    code: String(p.code ?? ""),
    message: String(p.message ?? ""),
    entity_type: String(p.entity_type ?? ""),
    entity_id: String(p.entity_id ?? ""),
    field: typeof p.field === "string" ? p.field : null,
    label: typeof p.label === "string" ? p.label : null,
  }));
}

export function normalizeError(status: number, body: unknown): ApiError {
  const detail = detailOf(body);
  const text = typeof detail === "string" ? detail : undefined;

  if (status === 401) return { kind: "unauthorized", status: 401, message: text ?? GENERIC[401] };
  if (status === 403) return { kind: "forbidden", status: 403, message: text ?? GENERIC[403] };
  if (status === 404) return { kind: "not_found", status: 404, message: text ?? GENERIC[404] };
  if (status === 409) {
    if (isRecord(detail)) {
      return {
        kind: "conflict",
        status: 409,
        message: typeof detail.message === "string" ? detail.message : GENERIC[409],
        code: typeof detail.code === "string" ? detail.code : undefined,
        problems: toProblems(detail.problems),
        currentVersion: typeof detail.current_version === "number" ? detail.current_version : undefined,
        transactionIds: Array.isArray(detail.transaction_ids) ? detail.transaction_ids.filter((id): id is string => typeof id === "string") : [],
      };
    }
    return { kind: "conflict", status: 409, message: text ?? GENERIC[409], problems: [], transactionIds: [] };
  }
  if (status === 422) {
    const { fieldErrors, formErrors } = validationErrors(detail);
    return { kind: "validation", status: 422, message: GENERIC[422], fieldErrors, formErrors };
  }
  if (status >= 500) {
    return { kind: "server", status, message: "The server could not complete the request. Try again." };
  }
  return { kind: "client", status, message: text ?? `The request was rejected (${status}).` };
}

export function networkError(): ApiError {
  return { kind: "network", status: 0, message: "Could not reach the server. Check your connection and try again." };
}
