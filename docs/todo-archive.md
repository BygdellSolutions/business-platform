# TODO archive

Finished work moved out of `TODO.md` on 2026-10-14, kept in its original wording and grouped by the section it came from.
`CHANGELOG.md` is the record of what changed; this file only preserves the task history and decisions written at the time.

## Product changes from staging testing (ACTIVE milestone; D5 and deployment hardening are PAUSED, production stays NO-GO)

- [x] **Slice 0: viewers are read-only** for customers, items, horses, transactions/lines and custom-field values (backend `record_writer` + route inventory test; read-only detail views, no create links and a read-only transaction editor in the frontend)
- [x] Slice 0b: organization time zone setting and "today in the organization" (transaction and invoice date defaults; needed by date-limited discounts and service dates)
- [x] Slice 1: audit core (append-only, tenant-owned change log: who, when, record, action, changed fields with old/new values; `created_by`/`updated_by`; old rows "not recorded")
- [x] Slice 2: Customer, Horse and Item history (log writes + history UI)
- [x] Slice 3: transaction and invoice audit metadata
- [x] Slice 4: "recent authentication required" (a short-lived re-authentication, today by password; future SSO/passkey users use their own method) for leave, transfer and delete; Danger Zone at the bottom of Organization Settings: leave, ownership transfer, sole-owner rules, hard delete of the organization with strong confirmation; remove the header Leave button. Plus an **Account security** page under the USER (not the organization): change password (current, new, confirm; normal policy; other sessions revoked, the current one kept, as `change_password` does today)
- [x] Slice 5: owned-organization limit (`max_owned_organizations`, replacing `can_create_organizations`) on every path to ownership (create, transfer, promote, owner invitation) in the backend; "Owned 1 / 1" in the UI; one core check under the user-row lock plus a database backstop; an owner invitation accepted at the limit is refused whole and stays pending
- [x] Slice 6: line discount foundation (ordered discount layers with the base and final unit price on transaction and invoice lines, CHECKs, invoices copy them verbatim)
- [x] Slice 7: customer permanent discount (owner/admin, history, applied when a line is added)
- [x] Slice 8: catalog temporary discounts (start/end in the organization time zone, base price untouched, always stacked before the customer discount)
- [x] Slice 9: Service line type (Catalog item / Service / Ad-hoc; service, person or animal, performed by, performed at; visible from the horse and customer)
- [x] **Inventory track (stock, backorders, incoming): APPROVED 2026-10-08, built 2026-10-08 (I1-I6), after slice 9 and before slices 10-13.** Design and decisions: `docs/architecture.md`, "Inventory and backorders (approved, NOT implemented)". Insufficient stock is a warning, never a blocking error; physical stock never goes negative; stock movements explain physical stock, backorders explain unmet demand.
  - [x] I1: Inventory module foundation. `track_stock` and `sku` on items (products only; services never hold stock); an append-only, tenant-owned stock movement ledger (receipt, fulfillment, adjustment with reason, who and when), so every on-hand figure is explainable; opening stock is an audited adjustment; a per-item row lock serializes everything that allocates. Core gains lifecycle EFFECTS next to validators (an architectural extension: Sales must stay unaware of Inventory, so Inventory reacts to complete/reopen/cancel through core, the same way Invoicing vetoes today).
  - [x] I2: availability on the transaction line. Show on hand / committed / available / incoming when a catalog item is added; a non-blocking warning ("Only 5 of 8 available, 3 will be backordered"); the backend returns the shortage and never refuses for stock.
  - [x] I3: completion effect: under the item locks, deliver what is available (delivery movement) and backorder the shortage; lines keep ordered / delivered / backordered / later-fulfilled quantities; reopen and cancel return delivered units (return movement) and cancel open backorders
  - [x] I4: incoming stock (item, quantity, expected date, supplier, reference, created by/at) and goods receipt (incoming becomes on hand; backorders become "Ready to fulfill").
  - [x] I5: backorder backlog view and allocation (oldest first, proposed and then confirmed by a person; states Waiting for stock / Partially fulfilled / Ready to fulfill / Fulfilled / Cancelled).
  - [x] I6: catalog/warehouse columns (SKU, on hand, committed, available, incoming, backordered, low-stock threshold, status; low stock, out of stock, backordered and incoming are separate states that can hold at the same time); dashboard figures come with slice 13.
  - Decided: stock is set aside at COMPLETION (drafts only warn); available units are delivered at completion and only the shortage waits; the whole transaction is invoiced at once, backordered units included (fulfillment and invoicing stay separate; an "invoice only fulfilled items" mode may come later); receipt never fulfills by itself, a person confirms the oldest-first proposal.
- [x] Slice 10: seller and payment profile (phone, email, website, bankgiro, plusgiro, IBAN, BIC, payment terms, F-skatt) and the document language setting; issuer snapshot schema 2
- [x] Slice 11: invoice PDF template v2 (professional layout, verified against Swedish invoice requirements, ML 2023:200 ch. 17; frozen v1 PDFs stay as they are)
- [x] Slice 12: invitation audit in the Members UI (invited by, invited at, state)
- [x] Slice 13: dashboard redesign for the organization owner
- [x] Payments recorded by hand (record, reverse, paid/outstanding, list filter, dashboard) — 2026-10-08
- [x] Credit notes (partial/full per line, reason, returned to stock, own number, Kreditfaktura PDF) and refunds — 2026-10-10
- [x] Return cases on the invoice (requested → goods received → approved / rejected, follow-up date, notes, "Returns to handle" on the dashboard; approval opens the credit form prefilled) — 2026-10-10
- [x] Suppliers register, chosen on incoming stock (migrated from typed names) — 2026-10-10
- [x] Staging feedback 2026-10-10: order date on a supplier's deliveries; catalog stock badges no longer wrap rows; catalog prices split (base, promotion, promotion price, incl. VAT) and entry incl. VAT; editable expected date and a unit cost on incoming stock
- [x] Catalog type "Charge" for travel, mileage and fees (owner chose it 2026-10-10)
- [x] Record numbers: orders from 1001; customers, suppliers, horses and catalog items from 1 (owner, 2026-10-10)
- [x] Sort every table by a column (server-side for paged lists, in place for the rest) — 2026-10-10
- [x] Counter sales: "Paid now" (Swish/card/cash, receipt) or "Invoice" (onto the customer's open draft) from a draft order; walk-in customer; dashboard — 2026-10-10
- [x] Friendlier screens, step 1: sidebar menu grouped by area with icons and the current page highlighted; one accent colour; filled primary buttons ("New …" and every form's Save) — 2026-10-11
- [x] Friendlier screens, step 2: every block of information in its own box (one shared `panel` style) — 2026-10-11
- [x] Backorders in plain words: "Deliver" button, Deliver links from the Inventory page; Paid now warns about a shortage; the receipt lists what is to be delivered later — 2026-10-11
- [x] Invoicing by delivery, slices 1-2: invoice what is delivered; the warning (invoice delivered now / wait until everything is delivered); later deliveries onto the draft; Ready to invoice; Partly invoiced / Waiting for delivery — 2026-10-11
- [x] Invoicing by delivery, slices 3-4: PDF template 11 ("Ordered 7 · Backordered 5" / "Beställt 7 · Restnoterat 5"); credit notes per invoice line of a split order line, stock returns capped per order line and shown per invoice line — 2026-10-11

## Now

- [x] Install Docker Desktop and run `docker compose up -d`
- [x] Verify `/health/db` returns `{"database":"ok"}` (backend can connect to PostgreSQL)
- [x] Set Git `user.name` / `user.email` and make the first commit
- [x] Verify frontend runs (`npm run dev`) and reaches the backend
- [x] Create Organization model and migration (start of Phase 1)
- [x] Users, organization memberships and development identity (`/api/me`, tenant context)
- [x] Tenant-scoped query helpers, Customer model/migration, minimal tenant-safe Customer API, cross-tenant tests
- [x] Catalog: Item model/migration, tenant-safe Items API, exact money/VAT handling, shared tenant-isolation contract
- [x] Equine module: Horse with Customer owner/stable references, composite tenant-safe FKs, module boundary test
- [x] Sales: Transactions (header + lines), item snapshots, stored CHECK-enforced line amounts, lifecycle actions, row locking, tenant contract
- [x] Custom fields (UDF) engine: core entity registry, lifecycle validation seam, `require_role`, typed value storage, dependent references, delete guard, required fields at completion

## Next

- [x] Frontend slice 1 (foundations): BFF, dev identity, `/o/{orgId}` shell, organization switcher, dashboard, test infrastructure and tenant-isolation e2e tests
- [x] Frontend slice 2: Customers and Catalog (list, search, filters, create, edit, deactivate/reactivate) with the shared UI primitives and tenant-isolation e2e tests
- [x] Frontend slice 3: Horses with Owner and Stable pickers (first reusable EntityPicker)
- [x] Sales optimistic concurrency (`If-Match` versions on transactions, headers and lines) with committed-data concurrency tests
- [x] Frontend slice 4: Transactions (list, create-then-edit, line editor, backend-owned totals, lifecycle, stale-tab handling)
- [x] Run `uv run alembic upgrade head` on the development database (migration `c41a7e5d9b20`); done before slice 5 (next entries)
- [x] Frontend slice 5: generic custom-field renderer, dependent references, completion problems mapped to lines and controls, read-only completed transactions
- [x] Development database migrated to `c41a7e5d9b20` (done before slice 5)
- [x] Invoicing design approved (recorded in `docs/architecture.md`, "Invoicing design (approved, NOT implemented)")
- [x] Invoicing prerequisites: organization and customer profiles, explicit currency (`default_currency`, transaction snapshot, safe migration, locked change), `UNIQUE (organization_id, id, transaction_id)` on `transaction_lines`, `reopen`/`cancel` lifecycle events, generic custom-field flag filter
- [x] Development database migrated to `e63c9a2b8d45` and reseeded (seeded organizations and transactions have SEK)
- [x] Invoicing backend: draft/issue lifecycle, counter-based numbering, immutability triggers, reservation through the core seam, snapshots, concurrency tests (migration `f74d0b3c9e56`)
- [x] Development database migrated to `f74d0b3c9e56`
- [x] Invoicing frontend V1 (list, create from transactions, draft editor, issue, issued view)
- [x] Invoice PDF: lazy-freeze artifact in `invoice_pdfs` (migration `a85e1c4d7f90`), ReportLab renderer with bundled Noto fonts, `GET /api/invoices/{id}/pdf`, BFF binary pass-through and "Download PDF" (committed as a557cce)
- [x] Development database migrated to `a85e1c4d7f90`. It now holds ONE permanent issued invoice (number 1, Fredrik Horse Therapy, from its seeded transaction) and its frozen PDF in `invoice_pdfs`, created by the PDF smoke test; issued invoices are immutable, so that transaction can no longer be reopened or cancelled in dev

## Production authentication and membership administration (COMPLETE: S1 to S5 committed; S6 was folded into the deployment-readiness milestone)

- [x] **S1 Backend identity core:** credentials, sessions, setup links, minimal `security_events`, Argon2id, login/logout/change-password/setup endpoints, bounded login-abuse protection, fail-closed `AUTH_MODE`, operator CLI, migration `b96f2d4e8a13` (committed as 17d77bb; development database migrated and unchanged otherwise)
- [x] **S2 Browser authentication:** BFF credential refactor (cookie to Bearer), login/logout/setup pages, `PUBLIC_ORIGIN` and double-submit CSRF, header stripping, session-mode Playwright run, `GET /api/me/user` (committed as cf2f0a9)
- [x] **S3 Organization onboarding (committed as d9953c8):** atomic `POST /api/organizations` (organization, owner membership, retry record and event in one transaction), the `can_create_organizations` gate with a fresh user-row lock, `Idempotency-Key` retries, onboarding page that requires a currency, operator `grant-org-creation` / `revoke-org-creation`, migration `c07a3e5f9b24` (committed; development database migrated to `c07a3e5f9b24`, post-S3 baseline recorded: no credentials, sessions, organizations, memberships, requests or events were created by the migration)
- [x] Migrated the development database to `c07a3e5f9b24` after S3 was committed (`uv run alembic upgrade head` against the DEV url; then optionally `grant-org-creation --email fredrik@dev.test` there, since the dev seed only gives the right to new seed data)
- [x] **S4 Membership administration (committed as 10cb40c):** member list, role change, removal and leave (owner/admin rules), one ordered lock over the organization's memberships with fresh re-reads, the deferred owner-loss trigger, operator `repair owner` command, migration `d18b4c6e2f31`
- [x] **S5 Invitations:** copy-link invitations (fragment tokens, hashed, shown once), administration with fresh-lock authority, accept flows for existing and new accounts, race-safe single-use acceptance, migration `e29c5d7a3b48` (approved and committed as `feat: add organization invitations`)
- [x] Development database migrated to `e29c5d7a3b48` right after the S5 commit (no invitations, users, credentials, memberships, sessions or events were created; no roles, owners or capabilities changed)
- [x] **S6 Hardening and documentation:** superseded by the production-deployment-readiness milestone (proxy/client-address trust, CSP and headers, throttling of the invite preview, scheduled purge); see `Deployment readiness` below
- [x] Development database migrated to `b96f2d4e8a13`; it has no credentials, so a manual session-mode login in development needs an operator link first (`python -m app.scripts.admin reissue-setup-link --email ...`), which would change permanent dev data and is done only on request

## Deployment readiness (D1-D4 built, D5 paused)

- [x] Architecture review approved (staging rehearsal first, Coolify-managed PostgreSQL 17, one Uvicorn worker, exact-head schema readiness, BFF internal secret and migration/runtime DB roles in D2, CSP deferred, GitHub-hosted CI, no production backups in ordinary CI)
- [x] **D1 Container foundation:** backend and frontend production images, `.dockerignore`s, standalone output, bundled local font (no Google fonts), prerendering of config-dependent pages eliminated, production exits on an unusable frontend configuration, local rehearsal compose, image and topology tests (implemented and verified; committed as 5688e03)
- [x] **D2** configuration/proxy/health/logging hardening: migration job with advisory lock (`python -m app.scripts.migrate`), `/health/ready` = exactly this image's Alembic head, `/api/health` and `/api/ready`, BFF internal secret, owner/app database roles (`bootstrap_roles`, reconcile), `hide_parameters`, production fail-closed configuration (backend and frontend), CORS `[]`, request id, JSON logs with redaction, baseline security headers and production HSTS, the BFF failure contract (implemented and verified; committed as 4578644)
- [x] **D3** CI (GitHub Actions, disposable infrastructure only): `ci.yml` (`workflows`, `backend`, `frontend`, `containers`, `e2e-dev`, `e2e-session`, aggregate `ci-gate`), `audit.yml` (scheduled, informational), static workflow tests, redacted failure logs only (implemented and verified locally; committed as 8f5e9e3 and earlier; the first real GitHub runs are still to be observed; the first real GitHub runs are still to be observed)
- [x] **D4** backup/restore tooling and runbooks: `backup` (custom-format dump + manifest, atomic, never overwrites), `restore` (scratch-database guards, `pg_restore` single transaction, role reconciliation), read-only `verify_restore` (schema, critical objects, data, every PDF, auth, runtime-role DML/DDL), `db_stats`, optional read-only backup role, automated drill in `deploy/drill` (real clients and backend image, weekly/manual workflow, not a gate), `docs/backup-restore.md` and `docs/deployment.md` (implemented and verified locally; committed as 83f0c5e)

## Scalability (local capacity probe 2026-10-13; production capacity not verified)

- [x] **Local capacity probe (2026-10-13):** built images tested against guarded scratch databases on the dedicated TEST server, with 1,000 session-authenticated accounts and real HTTP through the BFF. Results and reproducible commands: `docs/loadtest-2026-10-13.md`. The default deployment suffers pool/thread scheduling starvation under request bursts; the admission wrapper used in comparisons is an experiment only, not deployed application code.
