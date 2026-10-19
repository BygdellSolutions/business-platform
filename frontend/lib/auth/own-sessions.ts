/** The signed-in person's own sessions, as the account page shows them. Pure: no request, no cookie, no secret. */

export type OwnSession = { id: string; createdAt: string; lastUsedAt: string; userAgent: string | null; current: boolean };

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const MAX_ROWS = 100;
const MAX_AGENT = 200;

const isTimestamp = (value: unknown): value is string => typeof value === "string" && value.length <= 40 && !Number.isNaN(Date.parse(value));

/** FastAPI's list reduced to the five fields a person needs, or null if it is not what was promised (never passed on as is). */
export function parseSessionList(value: unknown): OwnSession[] | null {
  if (!Array.isArray(value) || value.length > MAX_ROWS) return null;
  const rows: OwnSession[] = [];
  for (const row of value as Record<string, unknown>[]) {
    if (typeof row !== "object" || row === null) return null;
    const { id, created_at: createdAt, last_used_at: lastUsedAt, user_agent: agent, current } = row;
    if (typeof id !== "string" || !UUID.test(id) || !isTimestamp(createdAt) || !isTimestamp(lastUsedAt) || typeof current !== "boolean") return null;
    if (agent !== null && (typeof agent !== "string" || agent.length > MAX_AGENT)) return null;
    rows.push({ id, createdAt, lastUsedAt, userAgent: agent, current });
  }
  return rows;
}

/** "Chrome on Windows" from a user-agent string, or the raw start of it; display only, never used to decide anything. */
export function describeBrowser(userAgent: string | null): string {
  if (!userAgent) return "Unknown browser";
  const browser = /Edg\//.test(userAgent) ? "Edge" : /OPR\/|Opera/.test(userAgent) ? "Opera" : /Firefox\//.test(userAgent) ? "Firefox" : /Chrome\//.test(userAgent) ? "Chrome" : /Safari\//.test(userAgent) ? "Safari" : null;
  const system = /Windows/.test(userAgent) ? "Windows" : /iPhone|iPad/.test(userAgent) ? "iOS" : /Android/.test(userAgent) ? "Android" : /Mac OS X/.test(userAgent) ? "macOS" : /Linux/.test(userAgent) ? "Linux" : null;
  if (browser && system) return `${browser} on ${system}`;
  return browser ?? system ?? userAgent.slice(0, 40);
}
