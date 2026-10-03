import "server-only";

import type { BulkValuesRead, Definition, ValueRead } from "@/lib/custom-fields/types";
import { serverRead } from "@/lib/server-api";

/** Ids per request: a comma-separated list in the address, and the backend allows 200. */
const IDS_PER_REQUEST = 100;

export interface EntityFields {
  definitions: Definition[];
  /** Set values by record id (a record without any set value is absent or empty). */
  values: Record<string, ValueRead[]>;
}

/**
 * The enabled definitions of one entity type and the values of the given records, read on the
 * server for the page (organization from the URL, identity from the cookie, as everything
 * else). With no definitions there is nothing to ask about values.
 */
export async function readEntityFields(orgId: string, entityType: string, entityIds: string[]): Promise<EntityFields> {
  const definitions = await serverRead<Definition[]>(orgId, "/api/custom-fields/definitions", `?${new URLSearchParams({ entity_type: entityType, limit: "200" })}`);
  const values: Record<string, ValueRead[]> = {};
  if (definitions.length === 0) return { definitions, values };

  for (let start = 0; start < entityIds.length; start += IDS_PER_REQUEST) {
    const ids = entityIds.slice(start, start + IDS_PER_REQUEST);
    const read = await serverRead<BulkValuesRead>(orgId, "/api/custom-fields/values", `?${new URLSearchParams({ entity_type: entityType, entity_ids: ids.join(",") })}`);
    Object.assign(values, read.entities);
  }
  return { definitions, values };
}
