import { NextResponse, type NextRequest } from "next/server";

import { authMode } from "@/lib/auth/config";
import { credentialFromRequest } from "@/lib/auth/credential";
import { clientAddress, originProblem, validCsrf } from "@/lib/auth/request";
import { backendFetch } from "@/lib/backend";
import { isSameOrigin } from "@/lib/origin";
import { instrument } from "@/lib/observability";
import { BODY_REFUSAL_MESSAGE, BODY_REFUSAL_STATUS, readBodyText } from "@/lib/request-body";
import { infrastructureFailure, isInfrastructureFailure, upstreamUnavailable } from "@/lib/upstream";

/**
 * The support page's message box. PUBLIC: someone who cannot sign in has to be able to write, so there is no session requirement.
 * What it does and does not do:
 *  - the request must come from this site (Origin); a message cannot be posted from another page on the visitor's behalf;
 *  - the body is bounded while it is read (4,000 characters can be 16 KB of UTF-8, plus the other fields);
 *  - a signed-in visitor's credential is passed on, with the CSRF echo in session mode, so the ticket can be tied to the account;
 *    a credential that is missing or does not verify just means "not signed in", never an error;
 *  - the browser's address is passed to FastAPI as the throttling key (it believes it only from this authenticated door);
 *  - only the ticket number, or a plain refusal, goes back; nothing else FastAPI says is relayed.
 */
const MAX_BODY_BYTES = 40_000;
const BODY_DEADLINE_MS = 15_000;
const NO_STORE = { "cache-control": "no-store" };

function failure(status: number, detail: unknown, headers: Record<string, string> = {}): NextResponse {
  return NextResponse.json({ detail }, { status, headers: { ...NO_STORE, ...headers } });
}

export const POST = instrument(async function POST(request: NextRequest) {
  const mode = authMode();
  if (mode === "none") return failure(503, "Support is not available right now.");
  const refused = mode === "session" ? originProblem(request) !== null : !isSameOrigin(request);
  if (refused) return failure(403, "Cross-origin requests are not allowed");

  if (!(request.headers.get("content-type") ?? "").toLowerCase().startsWith("application/json")) return failure(415, "Only JSON bodies are accepted");
  const declared = Number.parseInt(request.headers.get("content-length") ?? "0", 10);
  if (declared > MAX_BODY_BYTES) return failure(413, "Request body too large");
  const read = await readBodyText(request, MAX_BODY_BYTES, BODY_DEADLINE_MS);
  if (!read.ok) return failure(BODY_REFUSAL_STATUS[read.reason], BODY_REFUSAL_MESSAGE[read.reason]);

  // Signed in only if the credential AND, in session mode, the CSRF echo are there; otherwise the message is simply anonymous.
  let credential = credentialFromRequest(request);
  let csrf: string | undefined;
  if (credential !== null && mode === "session") {
    const valid = validCsrf(request);
    if (valid === null) credential = null;
    else csrf = valid;
  }

  let upstream: Response;
  try {
    upstream = await backendFetch({ credential }, "/api/support", { method: "POST", body: read.text, csrf, clientAddress: clientAddress(request.headers) ?? undefined });
  } catch (error) {
    return upstreamUnavailable(error);
  }
  if (upstream.status === 201) {
    const answer = (await upstream.json().catch(() => undefined)) as { reference?: unknown } | undefined;
    if (typeof answer?.reference === "string" && /^SUP-[A-Z0-9]{8}$/.test(answer.reference)) return NextResponse.json({ reference: answer.reference }, { status: 201, headers: NO_STORE });
    return failure(502, "Unexpected response from the backend");
  }
  if (upstream.status === 401) return failure(502, "Unexpected response from the backend"); // never a login redirect for a public box
  if (isInfrastructureFailure(upstream)) return infrastructureFailure(upstream);
  const wait = upstream.headers.get("retry-after");
  return new NextResponse(await upstream.text(), {
    status: upstream.status,
    headers: {
      "content-type": upstream.headers.get("content-type") ?? "application/json",
      ...NO_STORE,
      ...(wait !== null && /^[0-9]{1,5}$/.test(wait) ? { "retry-after": wait } : {}),
    },
  });
});
