import type { PickerSearch } from "@/components/ui/EntityPicker";
import { apiFetch } from "@/lib/api/client";
import type { ApiResult } from "@/lib/api/errors";
import type { Choice, Definition } from "@/lib/custom-fields/types";

/**
 * Browser-side calls to the Custom Fields API, through the BFF for the organization given.
 * Reference choices come from the generic choices endpoint of the DEFINITION; this layer never
 * asks any other module for them.
 */

export const CHOICES_LIMIT = 20;

/**
 * The search function of a reference field's picker. For a field that depends on another,
 * `dependsOn` is the parent's current value (a UUID) and the BACKEND returns only the matching
 * choices; with no parent value there are no choices (and no request). Only active targets are
 * offered: a new assignment of an inactive one would be refused anyway.
 *
 * A new function must be made whenever `dependsOn` changes (the picker treats a new function as
 * a new question and drops every answer to the old one).
 */
export function referenceSearch(orgId: string, definition: Definition, dependsOn: string | null): PickerSearch {
  const dependent = definition.reference?.depends_on != null;
  return async (query, signal) => {
    if (dependent && dependsOn === null) return { ok: true, status: 200, data: [] };
    const params = new URLSearchParams({ limit: String(CHOICES_LIMIT) });
    if (query !== "") params.set("q", query);
    if (dependent && dependsOn !== null) params.set("depends_on_value", dependsOn);

    const result = await apiFetch<Choice[]>(orgId, `/custom-fields/definitions/${encodeURIComponent(definition.id)}/choices?${params.toString()}`, { signal });
    if (!result.ok) return result;
    return { ok: true, status: result.status, data: result.data.map((choice) => ({ id: choice.id, label: choice.label, inactive: !choice.active })) };
  };
}

/**
 * Save changed values of ONE record in ONE request: `{"values": {"<key>": value or null}}`.
 * Custom-field writes carry no record version: they are outside the optimistic-concurrency
 * contract of the records they belong to (the backend locks the record, so a write and a
 * lifecycle step cannot interleave).
 */
export function writeValues(orgId: string, entityType: string, entityId: string, values: Record<string, string | boolean | null>): Promise<ApiResult<unknown>> {
  return apiFetch<unknown>(orgId, `/custom-fields/entities/${encodeURIComponent(entityType)}/${encodeURIComponent(entityId)}/values`, {
    method: "PATCH",
    body: { values },
  });
}
