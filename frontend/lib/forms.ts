import { useRef, useState } from "react";

import type { ApiError, ApiResult, FieldErrors } from "@/lib/api/errors";

/**
 * Shared behavior of the create/edit forms and one-click actions:
 *   - `useMutation` runs one request at a time (a double click cannot send twice), exposes
 *     `pending`, and keeps the last ApiError;
 *   - `problemsFrom` splits that error into messages for specific controls (a 422 location
 *     equal to a control's name) and general messages for everything else, so no backend
 *     message is ever lost just because no control carries that name.
 */

export interface Problems {
  /** Messages for controls, keyed by control name (= API field name). */
  byField: FieldErrors;
  /** Everything that does not belong to one control. */
  general: string[];
}

/** A blank optional field means "no value" (null), not an empty string. */
export function blankToNull(value: string): string | null {
  return value.trim() === "" ? null : value;
}

/** The one local message for a decimal field that is not a decimal at all (the backend judges the rest). */
export const NOT_A_DECIMAL = "Enter a number such as 850.00, using a dot as the decimal separator.";

export const NO_PROBLEMS: Problems = { byField: {}, general: [] };

export function problemsFrom(error: ApiError | null, controls: readonly string[]): Problems {
  if (error === null) return NO_PROBLEMS;

  switch (error.kind) {
    case "validation": {
      const byField: FieldErrors = {};
      const general = [...error.formErrors];
      for (const [path, messages] of Object.entries(error.fieldErrors)) {
        if (controls.includes(path)) byField[path] = messages;
        else general.push(...messages.map((message) => `${path}: ${message}`));
      }
      return { byField, general };
    }
    case "not_found":
      // The record is gone, or was never in this organization: deliberately the same words.
      return { byField: {}, general: ["This record no longer exists, or you do not have access to it."] };
    default:
      return { byField: {}, general: [error.message] };
  }
}

export function useMutation() {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<ApiError | null>(null);
  const inFlight = useRef(false);

  /** Resolves to the response data on success, or null (with `error` set) on failure. */
  async function run<T>(call: () => Promise<ApiResult<T>>): Promise<T | null> {
    if (inFlight.current) return null;
    inFlight.current = true;
    setPending(true);
    setError(null);
    try {
      const result = await call();
      if (result.ok) return result.data;
      setError(result.error);
      return null;
    } finally {
      inFlight.current = false;
      setPending(false);
    }
  }

  return { pending, error, run };
}
