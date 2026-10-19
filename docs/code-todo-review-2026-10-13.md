# Code and TODO review — 13 October 2026

The modular monolith is a useful foundation for development productivity, security and growth. Keep the current stack. The main gaps are bounded resource use, dashboard query cost, operational readiness and a work queue that mixes current work with obsolete statements. The code does not yet establish capacity for 1,000 actively navigating users.

This review examined authentication, tenant scope, BFF request handling, dashboard reads, PDF handling, deployment/CI definitions, the TODO and the existing local capacity results. It is a targeted review, not an exhaustive security audit. Live hosting settings, branch protection, current dependency advisories and production recovery were not verified.

## Findings, in priority order

### 1. High: request body limits do not bound reads or consistently count bytes

`frontend/lib/auth/handlers.ts:44` calls `request.text()` before checking the size. The organization-creation and tenant BFF routes do the same, with an additional early Content-Length check. A request without that header still gets fully buffered. All three compare JavaScript string length with a constant named `MAX_BODY_BYTES`; that measures UTF-16 code units, not incoming UTF-8 bytes.

A small local probe of the actual `readJsonObject` helper confirmed both behaviours: a 102,400-byte stream was entirely consumed before rejection against its 16,384 limit; valid JSON containing 18,011 UTF-8 bytes / 6,011 code units was accepted. The probe did not contact a running service or attempt memory exhaustion. Deployment proxy limits may reduce exposure but were not verified.

**Correction:** use one bounded stream reader that counts bytes, stops/cancels at the limit and handles interrupted reads safely. Apply route-appropriate proxy body limits and read deadlines too. Test missing Content-Length, chunked/streamed input, multibyte JSON, slow input and cancellation. This addresses an availability weakness consistent with [OWASP's request-size and resource-limit guidance](https://cheatsheetseries.owasp.org/cheatsheets/Denial_of_Service_Cheat_Sheet.html).

**TODO alignment:** newly recorded under SaaS security gaps; previously only general rate limits were listed.

### 2. High: the demonstrated overload correction is still outside the application

`backend/app/core/db.py:12` sets a connection-establishment timeout but does not explicitly configure checkout, query or lock-wait timeouts. `backend/app/main.py` has no whole-request admission gate and handles OperationalError/InterfaceError, but not SQLAlchemy pool TimeoutError. `backend/tools/admission_app.py` remains an experimental wrapper. The backend image still starts one worker.

The [existing capacity report](loadtest-2026-10-13.md) records near-total failure at the 100-request burst with the default configuration, and 19.9% timeouts in the paced 1,000-account API scenario. These are earlier measurements, not tests rerun during this review.

**Correction:** ship reviewed admission, explicit resource budgets/timeouts, safe overload responses and recovery/cancellation tests first. Then size API workers and their combined connection/password-hashing budgets. Prove recovery without restarting containers. Admission must also leave capacity for health checks and operational work.

**TODO alignment:** accurately tracked in Scalability; the task is not complete merely because the experiment succeeded.

### 3. High: dashboard and invoice eligibility cost grows with tenant history

`backend/app/modules/invoicing/api.py:175` loads every candidate, computes eligibility and only then slices a page in Python. Its summary at line 214 loads all eligible transactions before calculating totals. `backend/app/modules/inventory/api.py:255` also loads all tracked products for its summary. These paths need bounds or efficient aggregation even when a displayed list contains only a few rows.

The earlier four-worker/admission experiment passed about 100 simple API requests/s but failed 1,325 of 2,000 full dashboard renders (66.25%). This is the most relevant evidence against declaring the current application ready for the proposed dashboard workload. The fixture does not establish capacity for substantial invoice, payment or stock histories.

**Correction:** move eligibility/filtering/paging and suitable aggregates into tenant-scoped SQL or maintained summaries; measure query plans and query counts with realistic histories. Preserve financial rounding, delivery and credit rules in parity tests. Then benchmark Redis/Valkey for shared informational results, including cold-cache and unavailable-cache behaviour.

The frontend already uses direct private API reads and request-local React memoization for membership/current-user reads (`frontend/lib/orgs.ts`). Check which calls actually repeat before adding another cache. Preserve fresh backend authorization on every request.

**TODO alignment:** invoice work and cache safety are well covered; include inventory and populated financial histories in the same performance review.

### 4. High before public launch: security and recovery gates remain operationally unfinished

The repository's defaults keep `TRUSTED_PROXY_HOPS=0` and `TRUST_CLIENT_IP_HEADER=false`. With those defaults, browser logins share the BFF's source address. The default source budget is 30 failed logins per 900 seconds; unrelated users can therefore exhaust a shared login budget. This is a conditional deployment finding: the live environment was not inspected. Enabling trust without verifying the proxy chain would introduce a different problem.

The TODO correctly leaves verified proxy trust, global operator session revocation after restore, scheduled/off-host backups and a real-platform restore drill open. CSP, broader rate limits, MFA and security-event alerting are also planned. The image audit remains informational, and image tags float; neither reviewed advisory status nor branch protection can be inferred from a workflow file.

**Correction:** make a single explicit launch checklist with evidence for each gate, including verified proxy addresses and monitoring. Preserve the current production NO-GO until those requirements and capacity acceptance are met. This review does not resume D5 or email delivery.

There is already a small opportunistic auth cleanup on successful login (`backend/app/core/auth_service.py:81`). The missing item is reliable scheduled cleanup, not the complete absence of cleanup.

### 5. Medium: the worker plan needs proof of transaction and retry behaviour

PostgreSQL plus Procrastinate is a reasonable fit for the planned background tasks, and separate queues fit interactive responsiveness. However, choosing the same database does not itself make job creation atomic with SQLAlchemy writes. The queue must use the same transaction, or a transactional outbox must bridge them.

[Procrastinate documents external-connection deferral](https://procrastinate.readthedocs.io/en/stable/howto/production/external_connection.html). Its SQLAlchemy example uses psycopg2; this repository uses psycopg 3. Confirm and test the exact supported integration/version, including session flush/commit ownership. This is an implementation requirement, not a finding that Procrastinate is incompatible.

**Correction:** prove commit produces both business data and a job, rollback produces neither, and worker crashes/retries cannot duplicate a financial or stock effect. Include real separate-process worker tests; inline-only tests cannot establish job claiming, crash recovery or tenant fairness. Measure throughput alongside normal API traffic; the TODO's generic jobs-per-second estimate is not application capacity evidence.

**TODO alignment:** the design already asks for atomic enqueue and idempotency; an explicit integration acceptance item has been added. Email remains paused.

### 6. Medium: the TODO no longer reliably answers what is unfinished

Concrete contradictions include:

- An unchecked migration to `c41a7e5d9b20` followed by a completed entry for that same migration.
- Deployment readiness labelled "nothing implemented" above completed D1–D4 implementation entries.
- Real authentication and a password-change page listed as missing despite session authentication and `frontend/app/account/page.tsx` existing.
- Missing column sorting, payments and credit notes in old technical-debt entries despite completed entries and implementations above.
- The independent worker foundation nested under the paused Email heading, while Scalability calls for that foundation separately.

**Correction:** archive historical completed items, reconcile obsolete debt, and split short current/next queues from later ideas and launch gates. Keep uncertainty about live configuration or old development-database steps explicit; do not mark them complete from code inspection. Extract the worker foundation from the email-delivery milestone without implicitly resuming email. This cleanup prevents duplicate work and makes reviews and estimates more dependable.

### 7. Medium as shared editing grows: concurrency protection is uneven

Sales uses versions/If-Match. Customers, items, organization settings and custom-field values remain last-writer-wins, as the TODO acknowledges. More users editing the same tenant increases the chance of silently overwriting another person's change.

**Correction:** extend conflict detection first to frequently co-edited or consequential fields, such as prices and organization settings, using the existing Sales pattern. Prioritize this by actual shared-editing workflows rather than total account count.

## What already aligns well

| Area | Evidence in the code | Preserve as the application grows |
|---|---|---|
| Development productivity | Modular monolith; tested import boundaries; shared tenant/query and BFF helpers; migration files; CI definitions | Small vertical slices, shared contracts, generated API types when useful; avoid a framework rewrite |
| Tenant security | Backend membership resolution, tenant-scoped helpers, composite tenant references and isolation tests | Apply the same boundaries to caches, jobs, PDFs and exports |
| Authentication | Opaque hashed sessions, Argon2id with bounded hashing, backend session-bound CSRF, exact-origin BFF checks, fresh active-user checks | Keep authorization authoritative when optimizing reads |
| Database/financial integrity | Restricted runtime role, row locks, financial constraints and immutable issued-document snapshots/artifacts | Test real commits, retries and concurrency as worker execution is introduced |
| Operational foundation | Non-root containers, redacted logs, schema readiness, backup/restore tools, disposable CI definitions | Finish and verify the real deployment/runbook steps |
| Scaling plan | Explicit workloads, measured overload findings, cache failure cases and resource budgets | Require whole-page success and recovery evidence, not only API throughput |

## Recommended work order

1. Reconcile the TODO into an actionable queue while preserving paused milestones and launch blockers.
2. Fix request body bounds, whole-request admission and database timeouts; establish useful resource/error metrics.
3. Optimize dashboard/eligibility work and size API workers against CPU, memory and database budgets.
4. Build and verify the worker foundation and test the Redis/Valkey pilot as separate small changes. Add conflict protection where shared editing warrants it.
5. Complete the outstanding deployment/security/recovery gates when that milestone resumes, then run the planned production-sized 30-minute API and full-page acceptance tests.

## Verification during this review

- Existing frontend suites: `lib/auth/request.test.ts`, `lib/auth/boundary.test.ts`, `lib/backend-auth.test.ts`, `lib/runtime-config.test.ts`, `lib/auth/config.test.ts`: **489 tests passed across five files**.
- Small in-process request-body probe described in finding 1; no network or database access.
- Backend code and test definitions inspected; backend database tests, browser E2E, dependency scans and load tests were not rerun.
- Application code and deployment configuration were not changed. Documentation records findings and follow-up work; it does not implement the corrections.
