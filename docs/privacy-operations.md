# Privacy operations (DRAFT, 2026-10-15)

**Status: a working draft written from what the application actually does today. It is not legal advice and has not been
reviewed by a lawyer or by IMY guidance specialists. Every statement marked "to confirm" needs the owner's decision or
outside advice before it is published or relied on.** It feeds launch gates G11 (GDPR procedures) and G20c (incident
procedure) in `TODO.md`.

Contact for everything below: `support@bygdell.solutions` (the owner).

## 1. Who is responsible for what (to confirm)

| Activity | Role of the platform operator (Bygdell Solutions) | Role of the customer organization |
|---|---|---|
| Website visitors, sign-up, billing, account and login data of the people who use the service | controller | none |
| Business records an organization keeps in the platform (its customers, suppliers, horses, journals, orders, invoices, payments, stock) | processor | controller |
| Security logs, backups, hosting | controller for the log content about accounts; processor for tenant data inside backups | controller of its own data |

Consequences to confirm: a **processor agreement** is concluded with every customer organization (terms or a separate DPA);
a person who writes to the operator about a customer organization's records is told to contact that organization, and the
operator helps the organization answer (section 3).

## 2. What is stored, for how long, and how it leaves

| Data category | Examples | Retention today | How it is removed |
|---|---|---|---|
| Account | email, name, password hash (Argon2id), organization memberships, creation allowance | while the account exists | **No self-service account deletion yet** (gap G11). An operator can disable an account; erasure of a person who has no other duty to keep records needs a defined procedure (section 3). |
| Sessions | hashed session token, created/last used, user agent, coarse source | until they end, plus `AUTH_RECORD_RETENTION_DAYS` (30 days) | `admin purge` (**nothing schedules it yet**, gap), or when the account's organization data is deleted |
| Security events | logins, failures, password changes, owner changes, session revocations | `SECURITY_EVENT_RETENTION_DAYS` (30 days; the database refuses deleting younger rows) | `admin purge` (**not scheduled yet**) |
| Organization records | customers, suppliers, items, horses and notes, orders, stock, custom fields, member list, invitations | while the organization is active | **Organization hard delete** removes all of it (owner action with recent re-authentication and a typed confirmation); the owner should export first (gate G24, not yet built) |
| Invoices, credit notes, receipts and their PDFs | issued documents, frozen PDFs, payments | while the organization is active (the organization, as controller, must itself meet its bookkeeping retention duties: to confirm with an accountant) | organization hard delete; no separate per-document deletion by design (issued documents are immutable) |
| Horse journals (planned) | visit notes, body-diagram marks | while the organization is active (owner decision 2026-10-14) | organization hard delete; no horse deletion (horses are only active/inactive) |
| Request logs | one JSON line per request: id, method, path (no query string), status, duration | held by the hosting platform's container logging; **retention not configured** (gap, to confirm with Coolify) | log rotation of the host |
| Backups | nightly dumps of the whole database | planned 14 daily / 8 weekly / 6 monthly (not yet running) | expire by age; a deleted organization can therefore remain in backups for up to about six months; this must be stated in the privacy notice (gate G20a) and deletions must be re-applied after any restore |
| Website analytics | none (off until assessed, TODO "Public website") | n/a | n/a |

## 3. A person asks about their data (access, correction, erasure, restriction, objection, portability)

Target: answer within one month; extend only where the law allows and tell the person why.

1. **Receive** at `support@bygdell.solutions`; log the date received and who handles it.
2. **Verify identity** before disclosing anything: reply to the address the account is registered with, or ask the person to
   sign in and confirm from inside the application. Never act on an unverified request, and never disclose another person's data.
3. **Decide whose data it is.**
   - Data about the person's own *account* (name, email, sessions, security events): the operator answers.
   - A person who appears as a *customer, supplier or horse owner in an organization's records*: the organization is the
     controller. Tell the person which request goes to which organization (without revealing that other organizations hold
     nothing), and offer the organization the export and deletion help it needs.
4. **Fulfil.** Today this is operator-assisted (no self-service tools yet): an export is a database query scoped to the one
   organization or account; erasure of an account is a manual, audited operation that must respect records the organization
   must keep (an invoice's customer snapshot stays with the invoice). Record what was done and the date.
5. **Close** by telling the person what was done and what was not, with the legal reason.

**Gaps to build (tracked in TODO under "SaaS security gaps"):** a tenant-safe organization export, an account-level export,
a defined and tested account-erasure procedure, and the retention jobs above.

## 4. A security incident (G20c)

Roles: the operator (owner) decides and acts; there is no on-call rota.

1. **Detect and record.** Note the time, what was seen and by whom (an alert, a customer report, a log finding).
2. **Contain.** Choose what applies:
   - end every session: `python -m app.scripts.admin revoke-all-sessions --yes-end-all-sessions`;
   - disable one account: `admin disable-user --email ...`;
   - rotate secrets (`SECURITY_KEY`, `BFF_INTERNAL_SECRET`, database role passwords) and redeploy;
   - if the database may be corrupted or tampered with, recover into a new database from a verified backup
     (`docs/backup-restore.md`), then revoke all sessions.
3. **Assess.** What data, whose, how many people, which organizations, is it still happening. Use the request id in the logs
   to follow a request; the security events show logins and ownership changes.
4. **Notify.**
   - As **processor**: tell each affected customer organization (the controller) without undue delay, with what is known.
   - As **controller** (account data): if the incident is likely to put people at risk, notify IMY within 72 hours of becoming
     aware; if the risk is high, tell the affected people without undue delay (to confirm the thresholds with advice).
5. **Record** every incident, including those not reported, with cause, effect and measures; **review** what to change.

## 5. Providers and places data goes (to complete)

| Provider | Purpose | Data | Location | Agreement |
|---|---|---|---|---|
| Hosting server and Coolify | runs the application and the database | everything | to confirm | to confirm |
| Cloudflare | DNS, proxy, bot protection, Access for staging | request metadata, traffic passes through | global network | to confirm |
| GitHub | source code, CI | no production data by rule | to confirm | to confirm |
| Stripe (planned) | subscription payments | billing data, never card numbers on our servers | to confirm | to confirm |
| Email provider (planned) | transactional email | recipient addresses and message content | to confirm | to confirm |

Fill in the location, any transfer outside the EU/EEA, the transfer mechanism and the signed agreement for each row before launch
(gate G20b).
