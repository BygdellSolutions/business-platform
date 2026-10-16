import "server-only";

import type { History } from "@/lib/api/types";
import { REFERENCE_FIELDS } from "@/lib/history-labels";
import { serverRead, serverReadOrNull } from "@/lib/server-api";

export interface RecordHistoryData {
  history: History;
  /** id -> name for the customers and items that history entries point at (ids that no longer exist are absent). */
  names: Record<string, string>;
}

/**
 * A record's history, read on the server, plus the names of the customers and items its changes refer to (a
 * history entry stores ids; a person wants to read "Anna Andersson"). Names are looked up in the same
 * organization through FastAPI; a record that was deleted since simply has no name.
 */
export async function readRecordHistory(orgId: string, entityType: string, entityId: string): Promise<RecordHistoryData> {
  const history = await serverRead<History>(orgId, "/api/history", `?${new URLSearchParams({ entity_type: entityType, entity_id: entityId })}`);
  const wanted = new Map<string, "customers" | "items" | "horses" | "people">();
  for (const event of history.events) {
    for (const [field, change] of Object.entries(event.changes)) {
      const area = REFERENCE_FIELDS[field];
      if (!area) continue;
      for (const value of [change.from, change.to]) if (typeof value === "string") wanted.set(value, area);
    }
  }
  // People are not records with a page of their own: their names come from the organization's member list.
  const people = [...wanted.values()].includes("people") ? await serverRead<{ user_id: string; name: string }[]>(orgId, "/api/members/people") : [];
  const found = await Promise.all(
    [...wanted].map(async ([id, area]) =>
      [id, area === "people" ? people.find((person) => person.user_id === id)?.name : (await serverReadOrNull<{ name: string }>(orgId, `/api/${area}/${id}`))?.name] as const,
    ),
  );
  return { history, names: Object.fromEntries(found.filter(([, name]) => name !== undefined)) as Record<string, string> };
}
