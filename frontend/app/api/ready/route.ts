import { NextResponse } from "next/server";

import { backendReady } from "@/lib/backend";
import { instrument } from "@/lib/observability";

/**
 * READINESS of the whole chain: the backend is reachable AND ready (its database answers and is at exactly the
 * backend image's Alembic head). Deployment gating asks this; a container health check must NOT (a temporary backend
 * outage does not mean this process died: see /api/health).
 *
 * The answer is public and coarse on purpose: "ready" or "unready", nothing else. No database URL or revision, no
 * backend hostname, no exception text; the reasons are in the server logs.
 */
export const GET = instrument(async function GET() {
  const ready = await backendReady();
  return NextResponse.json({ status: ready ? "ready" : "unready" }, { status: ready ? 200 : 503, headers: { "cache-control": "no-store" } });
});
