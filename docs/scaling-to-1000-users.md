# Plan for 1,000 users

Consolidated on 13 October 2026 from the [local capacity measurements](loadtest-2026-10-13.md), Redis discussion and architecture review. This is an implementation and validation plan. The admission wrapper and four-worker configuration tested so far are experiments; Redis has not been benchmarked or integrated.

## Target and evidence

Use **1,000 active accounts** as the planning target, with an explicit interaction rate:

- Routine API scenario: one request per user every ten seconds, approximately 100 requests/s.
- Full dashboard scenario: one complete dashboard visit per user every 30 seconds, approximately 33.3 pages/s, including all the page's API calls.
- Headroom goal: approximately 67 complete dashboard pages/s for the same fixture and correctness requirements.

These are proposed engineering targets, not a claim that every user always behaves this way. Registered accounts, active sessions and simultaneous in-flight requests are different quantities. A burst of all users logging in at once is a separate workload.

| Measured scenario | Current deployment | Experimental admission plus four workers |
|---|---|---|
| 1,000 paced accounts, about 100 API requests/s | 19.9% timeouts | All 2,999 requests succeeded; p95 66 ms |
| Full dashboard visits, about 33.3 pages/s | Not measured in this configuration | 675 of 2,000 succeeded; 66.3% failed |
| Maximum simple PDF, 2,000 lines | Approximately 3.05 s to render | Background preparation not implemented |

The first failure is consistent with requests holding database sessions while synchronous dependency execution stalls. The full dashboard also consumes substantial CPU calculating summaries. Redis can reduce repeated summary work; it cannot replace request admission, efficient cache-miss queries or a database connection budget. Local tests used 16 logical CPUs, about 15.6 GiB RAM and tmpfs database storage, so production hardware must be measured separately.

## Architecture to retain

Keep the modular monolith: Next.js App Router, React, strict TypeScript and Tailwind for the frontend/BFF; FastAPI and SQLAlchemy for authoritative business logic; PostgreSQL for durable business data and sessions.

Apply the useful frontend principles by keeping rendering on the server where suitable, restricting client components to interactive needs, and reducing duplicate reads. Server components already call the private API directly through `serverRead`/`backendFetch`; preserve that path. [Next.js advises avoiding an extra trip through the app's own route handlers for server-component reads](https://nextjs.org/docs/app/guides/backend-for-frontend). Keep authentication, tenant scope and business invariants enforced in FastAPI.

Prisma, JWT sessions, moving all business logic into Next.js, and serverless hosting are separate architecture choices. The measured problems do not establish a need for those migrations. TypeScript and Tailwind support maintainable UI development; client bundle size was not identified as the cause of the server overload.

Planned additions are a shared Redis cache for selected read results, subject to its benchmark, and separate backend worker processes. The existing PostgreSQL/Procrastinate queue plan remains intact; Redis caching does not imply switching the job queue.

## Implementation order

| Order | Work | Required result |
|---|---|---|
| 1 | Request admission and database timeouts | Bursts produce bounded waiting and safe overload responses; ordinary requests continue and service recovers without restart |
| 2 | Worker configuration, routing and resource budgets | API processes, background workers and probes fit the database connection and machine CPU/memory budgets |
| 3 | Efficient summaries and page reads | Dashboard/eligibility paths avoid loading the entire backlog for every visit; repeated reads and API calls are reduced |
| 4 | Redis cache pilot | Repeated dashboard reads share calculated results safely across workers; cold-cache and outage behaviour remain bounded |
| 5 | Background jobs | Large PDF preparation and maintenance run with their own controlled capacity |
| 6 | Production-sized validation and operations | The complete 1,000-user workload passes on real hosting hardware, with monitoring, recovery and deployment checks completed |

### 1. Make overload predictable

- Implement whole-request admission before database-using synchronous dependencies. Bound running requests, waiting requests and waiting time; return a safe 503 with Retry-After when full.
- Keep per-process database-using concurrency below the available pool budget, leaving capacity for probes and other callers. The experiment's 12 running / 48 waiting / one-second wait is a starting point to test, not a permanent production value.
- Configure finite connection-pool checkout, database query and lock-wait timeouts; handle exhaustion explicitly. Verify cleanup on exceptions, cancellation, disconnects and failed writes.
- Test bursts and subsequent normal traffic without restarting containers. The existing probe restarts between stages and therefore has not proved automatic recovery.
- Add limited, delayed retries for safe reads where appropriate. Protect financial mutations with deliberate idempotency before enabling automatic retries.

### 2. Budget and add capacity

- Make API worker count configurable and validate multiple processes. Four workers were useful in the comparison; final count depends on the actual machine.
- For multiple containers, replace the fixed single-container upstream arrangement with verified routing/load balancing. Scale the Next.js frontend only when its measured CPU/latency requires it.
- Set pool size, overflow and timeout explicitly. Budget `API processes × maximum connections per process + background worker connections + probes/operators + reserve` below the usable PostgreSQL limit. Current defaults permit 60 API connections with four processes, before other consumers.
- Introduce PgBouncer if the measured connection budget and deployment pattern justify it; a replica count alone is not the decision criterion.
- Budget Argon2 hashing across the whole machine. Four processes with the current hashing admission permit up to 16 simultaneous hashes. Tune admission to CPU/RAM without weakening password security parameters to chase throughput.

### 3. Remove repeated dashboard work

- Profile the complete dashboard, not only individual API handlers. It also fetches organization, user/membership and other module summaries.
- Move invoice eligibility filtering, totals and pagination into tenant-scoped SQL or correctly maintained summaries. Inspect query plans and add indexes supported by those plans.
- Reduce repeated organization/membership reads within a render and batch reads where this reduces total work while preserving module boundaries and authorization. Parallel reads alone do not reduce their total cost.
- Apply bounded pagination to remaining load-all lists. Use realistic organization histories, including invoiced orders, payments, returns and inventory, when checking cost and correctness.
- Keep bundles small and preserve direct server-to-private-API reads. Frontend rendering improvements complement backend work; they do not replace it.

### 4. Test Redis for shared dashboard results

Use Redis to cache expensive **informational summary results** across API workers. [Redis's cache-aside pattern](https://redis.io/docs/latest/develop/use-cases/cache-aside/) supports expiry, explicit invalidation and protection against many simultaneous recalculations.

- Check the authenticated user's current membership/permissions before accessing a cached result. Include organization, requested period/filters, relevant permission scope and result version in cache keys; never let tenants share a result accidentally.
- Define acceptable freshness before implementation. A short expiry such as ten seconds is a pilot proposal for overview figures, not permission to show stale financial state everywhere. Refresh/invalidate affected summaries after successful business changes, with expiry as a backstop.
- Handle invalidation races: an older in-flight calculation must not repopulate the cache as current after a newer write. Test concurrent read/write behaviour and invalidation failures.
- Allow one calculation per key at a time with bounded waits, so cache expiration does not trigger a burst of duplicate database work.
- Keep Redis private, set a memory/eviction budget and use finite operation timeouts. A Redis failure falls back to efficient PostgreSQL queries under the same admission limits. Cached summaries are disposable; their loss must not lose business data.
- PostgreSQL remains authoritative for invoice issuance, payments, stock allocation and other business decisions. Do not use cached overview totals to approve those operations.
- Compare identical full-page tests with Redis disabled, warm, cold, expiring and unavailable. Test both many users sharing a tenant and users spread across many tenants; cache reuse can differ substantially.
- Retain Redis if it provides a useful measured benefit. A warm cache alone is insufficient evidence of 1,000-user readiness.

### 5. Isolate background work

- Implement the planned PostgreSQL-backed worker foundation with separate queues and concurrency budgets for PDF, maintenance and later email/bulk tasks.
- Prepare large PDFs after invoice issue in a separate worker and serve the existing stored artifact on download. Preserve the current artifact/version rules, tenant checks and race-safe behaviour; bound fallback rendering if an artifact is not ready.
- Schedule auth/security record and completed-job cleanup. Workers must be idempotent, tenant-scoped and included in connection budgets, with queue-depth/failure monitoring.
- Building the worker foundation does not resume paused email delivery. PDF object storage remains a size/backup-driven follow-up already tracked separately.

### 6. Prove capacity and operational readiness

Run on the real production-sized server through the intended proxy/TLS path. Use a separate disposable test database there, not live customer data. Test the deployed worker, routing, pool and cache settings.

Proposed acceptance criteria:

- Sustain the 100 API requests/s scenario and the 33.3 complete dashboard pages/s scenario for at least 30 minutes, with zero unexpected errors or capacity rejections at the target load. Include representative writes and populated invoice/stock workflows.
- Aim for p95 below one second for routine API calls and below two seconds for complete dashboard responses. Verify actual page content as well as HTTP status; Next.js can stream an error inside an HTTP 200 response.
- Demonstrate approximately double dashboard traffic as a headroom goal; record the measured limit rather than assuming linear worker scaling.
- Re-test cold caches, synchronized expiry, one large tenant and many distinct tenants. During Redis/database/worker outages and excess bursts, verify bounded failure, correct data and automatic recovery; any reduced availability is recorded explicitly.
- Exercise login/setup/invitation bursts, password hashing budgets, transaction/stock contention and large PDFs separately. Verify tenant isolation and that financial operations are not duplicated under retries.
- Use a generator with adequate headroom; repeat any stage where the generator saturates. Include browser/assets/network checks after the server-capacity tests.
- Record CPU, memory, response times/errors, admission rejections, pool waits, active database connections, slow queries, cache hit/miss/invalidation rates and job queue age/failures. Configure actionable alerts and document how to add capacity.
- Complete the existing production deployment requirements: verified proxy/client-IP trust, correct probes/routing, migrations, scheduled off-host backups and a real restore drill. Review recovery-point requirements as usage grows. Existing onboarding/authentication and support requirements remain separate launch work.

No specific VPS size is promised by this plan. Select and size hosting after measuring the final workload; the earlier local machine is not a production capacity guarantee.
