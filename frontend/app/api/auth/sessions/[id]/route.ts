import type { NextRequest } from "next/server";

import { callOwnSessions, json, problem } from "@/lib/auth/handlers";
import { instrument } from "@/lib/observability";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** End one of the signed-in person's own sessions (FastAPI answers 404 for anyone else's, an ended one and an unknown one alike). */
export const DELETE = instrument(async function DELETE(request: NextRequest, context: { params: Promise<{ id: string }> }) {
  const { id } = await context.params;
  if (!UUID.test(id)) return problem(404, "not_found", "That session does not exist.");
  const result = await callOwnSessions(request, `/api/auth/sessions/${id.toLowerCase()}`, "DELETE");
  if ("refused" in result) return result.refused;
  const { status } = result.upstream;
  if (status === 204) return json({ ended: 1 });
  if (status === 404) return problem(404, "not_found", "That session does not exist.");
  if (status === 401) return problem(401, "unauthenticated", "Sign in again.");
  return problem(502, "unavailable", "The session could not be ended right now.");
});
