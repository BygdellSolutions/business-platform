import { NextResponse } from "next/server";

import { instrument } from "@/lib/observability";

/**
 * LIVENESS: this Node process is up and answering. It depends on nothing (not the backend, not the database), so a
 * backend or database outage can never make an orchestrator conclude that THIS process is dead. Public and coarse.
 * (Readiness, which does ask the backend, is /api/ready.)
 */
export const GET = instrument(function GET() {
  return NextResponse.json({ status: "ok" }, { headers: { "cache-control": "no-store" } });
});
