# Deployment runbook (first deployment)

Status: **written in D4, before anything is deployed.** It states what the application and its tools require and what has been
proved locally. **It makes no claim about how Coolify behaves**: every statement that depends on the real platform is listed in
[D5 verification items](#d5-verification-items) and must be confirmed there before this runbook is trusted for production.

Backup, restore and disaster recovery are in [backup-restore.md](backup-restore.md); this file links to it where a deployment step
depends on it. Architecture background: [architecture.md](architecture.md) (D1 containers, D2 configuration/health/roles, D3 CI, D4 backup).

## 1. Topology

```text
Internet -> Coolify proxy (TLS ends here) -> frontend / BFF (the only public service)
                                             -> private FastAPI backend
                                                -> private Coolify-managed PostgreSQL 17
```

* The browser reaches only the frontend. The backend and the database are not published; the database is never exposed publicly.
* Two images are built from this repository: `backend/Dockerfile` and `frontend/Dockerfile`. **One image per service for every
  environment**: nothing about an environment is baked in; it is read from the environment when the process starts.
* The backend image also carries the **operator tools** (`python -m app.scripts.<tool>`): `migrate`, `bootstrap_roles`, `admin`,
  `backup`, `restore`, `verify_restore`, `db_stats`, and the PostgreSQL 17 client programs (`pg_dump`, `pg_restore`) the backup tools
  use. They run as one-shot jobs from the same image, with their own credentials, never inside the web process.

## 2. Production configuration

Everything comes from environment variables. Nothing secret is in the repository; `.env.example` holds placeholders only. Both
services refuse to start on an unsafe value and name the rule, never the value.

**Backend (web process)**

| variable | value |
|---|---|
| `APP_ENV` | `production` (no default: a process that is not told its environment does not start) |
| `AUTH_MODE` | `session` (the only production mode) |
| `DATABASE_URL` | the **app role** (DML only), `postgresql+psycopg://...` |
| `SECURITY_KEY` | at least 32 characters, random; HMAC key for login identifiers |
| `BFF_INTERNAL_SECRET` | at least 32 characters, at least 8 distinct, different from `SECURITY_KEY` |
| `PUBLIC_ORIGIN` | the public `https://` origin (used to print operator setup links) |
| `CORS_ORIGINS` | `[]` |
| `TRUST_CLIENT_IP_HEADER` | `false` until D5 (see section 12) |

It refuses `MIGRATION_DATABASE_URL`, `DEV_USER_EMAIL`, `TEST_DATABASE_URL`, `AUTH_MODE=dev|disabled` and any non-empty `CORS_ORIGINS`:
**a web process must not hold DDL credentials.**

**Frontend / BFF**: `APP_ENV=production`, `AUTH_MODE=session`, `PUBLIC_ORIGIN` (https), `BACKEND_URL` (a private/internal address or a
single-label service name), `BFF_INTERNAL_SECRET` (the same value as the backend's), `TRUSTED_PROXY_HOPS` (default `0`; section 12).

**Operator jobs** take exactly the variables named in their own section below (`MIGRATION_DATABASE_URL`, `BACKUP_DATABASE_URL`, ...).
A job is given only what it needs; none of them reads `DATABASE_URL` as a fallback.

## 3. Database roles

| role | purpose | used by | privileges |
|---|---|---|---|
| owner (migration) | owns the database and schema | `migrate`, `restore` | DDL; not a superuser, no CREATEDB/CREATEROLE |
| app | the web process | FastAPI (`DATABASE_URL`) | `SELECT/INSERT/UPDATE/DELETE` on tables, `USAGE/SELECT` on sequences; read-only on `alembic_version`; no DDL, no ownership |
| backup (optional, recommended) | the nightly dump | `backup`, `verify_restore`, `db_stats` | membership of `pg_read_all_data` and `CONNECT`: read-only, owns nothing |

`python -m app.scripts.bootstrap_roles` creates them. It needs one privileged connection (`BOOTSTRAP_DATABASE_URL`), is idempotent,
and runs **once per database, by an operator, never by the web process**:

```text
BOOTSTRAP_DATABASE_URL=postgresql://<admin>:...@<host>/<database> \
OWNER_ROLE=... OWNER_PASSWORD=... APP_ROLE=... APP_PASSWORD=... \
[BACKUP_ROLE=... BACKUP_PASSWORD=...] \
python -m app.scripts.bootstrap_roles
```

Role names and passwords are chosen per environment and stored in the platform's secret store, not in the repository. Which administrator
role the Coolify-managed database offers for this step is a D5 item.

## 4. Health: liveness versus readiness

| | endpoint | meaning |
|---|---|---|
| backend liveness | `GET /health` | the process runs; depends on nothing |
| backend readiness | `GET /health/ready` | the database is reachable **and its single Alembic revision equals this image's single head** |
| frontend liveness | `GET /api/health` | the process runs |
| frontend readiness | `GET /api/ready` | the backend is ready |

* **Exact head, in both directions.** A database newer than the image is as unready as one older than the image. An old image therefore
  cannot serve a database that a newer release migrated (see section 10, rollback).
* The backend image's `HEALTHCHECK` is readiness (a deployment gate must not call a not-yet-migrated container healthy). Nothing in the
  application exits on an unready answer. **An orchestrator that restarts unhealthy containers must be pointed at liveness (`/health`,
  `/api/health`), never at readiness**, or a database outage becomes a restart storm. Which probe Coolify uses for what is a D5 item.

## 5. The migration runner

`python -m app.scripts.migrate` is the only place a production schema changes. It never runs at web startup.

```text
MIGRATION_DATABASE_URL=postgresql+psycopg://<owner role>:...@<host>/<database> \
RUNTIME_DB_ROLE=<app role> APP_ENV=production \
python -m app.scripts.migrate
```

It takes a PostgreSQL advisory lock (a second job waits, `--lock-timeout`, default 600 s), refuses a database at a revision it does not
know, runs `alembic upgrade head` (never a downgrade), then reconciles the app role's grants. Exit codes: `0` migrated or already at head,
`1` failed, `2` configuration error, `3` lock timeout, `4` refused. There is no fallback to `DATABASE_URL` in production.

## 6. The pre-migration backup contract

**A migration of a database that holds data is preceded by a verified backup. This is a contract between the deployment procedure and the
backup tool; it is documented here and NOT wired into the migration runner, on purpose:**

1. take a backup (`python -m app.scripts.backup`, [backup-restore.md](backup-restore.md));
2. the backup command **must exit `0`** and its manifest must exist; a non-zero exit stops the deployment before the migration runs;
3. only then run the migration job.

The migration runner **never backs up silently**: a hidden dump inside a schema job would make the job's duration, disk use and failure
modes depend on database size. The orchestration of "backup, then (only on success) migrate" belongs to the deployment procedure and is
proved in D5; D4 provides and tests the backup tool and states the contract. A first deployment into an **empty** database has nothing to
protect and needs no backup.

The backup taken here is a *recovery* point, not an undo: see section 10.

## 7. First operator bootstrap

There is no public registration and no seed data in production. The first user is created by an operator with shell access to a
one-shot job of the backend image, configured like the backend (the command loads the web settings; it needs the same production
variables as the backend service, with `DATABASE_URL` the app role):

```text
python -m app.scripts.admin bootstrap-user --email owner@example.com --name "Ada Owner"
```

* It creates the user **without a password** (and allowed to create organizations; `--no-org-creation` to disable) and prints a **single-use link**
  `<PUBLIC_ORIGIN>/setup#<token>`. The person opens it and chooses their own password. No password is ever passed on a command line.
* The token is in the **URL fragment** (never sent to a server, never in a request log or referrer), stored only as a hash, valid for
  `SETUP_TOKEN_TTL_HOURS` (default **24**), and single use.
* **`PUBLIC_ORIGIN` determines the link.** Set it correctly before running the command; a wrong origin yields a link that points nowhere.
* **The link is printed to the command's standard output and is not written by the application's logger.** Run it in an interactive
  terminal/exec session, not as a scheduled or logged job, and do not paste it into tickets or chat. Whether the platform's terminal or job
  runner persists a command's output is a **D5 item**.
* Lost or expired: `python -m app.scripts.admin reissue-setup-link --email owner@example.com` (earlier links stop working).
* Other operator commands: `disable-user`, `enable-user`, `set-owned-limit`, `grant-org-creation`, `revoke-org-creation`, and `purge` (expired sessions,
  used or expired setup tokens, old security events; **scheduling it is a deployment task, not yet done**).
* The first owner then creates the organization in the application.

## 8. Deployment sequence (conceptual; the Coolify mechanics are D5)

1. Build and publish the two images from a green CI run.
2. **Back up** the database if it holds data; the backup must succeed (section 6).
3. Run the **migration job** with the new image. If it fails or is refused, stop: nothing else changes and the running version keeps serving
   (it stays ready only while the schema is still at its head).
4. Replace the backend with the new image; wait for **readiness**.
5. Replace the frontend; wait for its liveness and for `/api/ready`.
6. Smoke test through the public origin: sign in, read a customer, open an issued invoice's PDF.

The order "migrate before the new backend" means **a migration must be compatible with the previous release while it is still running**
(additive; no column the running version still needs may disappear in the same release). A release that cannot be additive needs a planned
maintenance window instead of this sequence.

## 9. Backups and recovery (summary)

Nightly logical backups (`pg_dump`, custom format) with a manifest, copied **off the database host** under the contract in
[backup-restore.md](backup-restore.md). **Recovery point objective: up to 24 hours of data.** Recovery is a restore into a *new, separate*
database, verified by `verify_restore`, followed by switching `DATABASE_URL`: never an in-place restore.

## 10. Rollback model

* **Application rollback** (running the previous image again) is allowed **only if the schema is still compatible with it.** Because
  readiness requires the **exact head**, an old image against a database that a newer release migrated is *unready by design*: it will
  not take traffic. Rolling back an image that is incompatible with the current schema is therefore not a switch you can flip; decide first.
* **There is no automatic `alembic downgrade`, and none is run by any tool.** Migrations are written to move forward.
* **The normal answer to a bad release is to fix forward**: ship a corrected release (a new migration if the schema needs repair).
* **Data recovery** (corruption, a destructive mistake, a lost database) is a restore **into a new, separate database**, verified with
  `verify_restore`, and only then `DATABASE_URL` is switched to it. The old database stays untouched until the new one is proved. A
  destructive in-place restore over the live database is **never** the normal path (backup-restore.md, "Disaster recovery").
* A failed migration job leaves the previous release running. Whether the previous schema is intact depends on the migration (PostgreSQL
  runs DDL in transactions, and each migration is applied that way); if in doubt, treat it as a data recovery.

## 11. What the deployment must not do

* Run the migration in the web process, or give the web process `MIGRATION_DATABASE_URL`.
* Give the app role ownership, DDL or superuser rights to make something work.
* Publish PostgreSQL or the backend.
* Put a database URL, password, token, setup link or key in the repository, an image, a build argument, a log or a ticket.
* Run backups from CI or store production backups in GitHub (backup-restore.md, "CI boundary").

## 12. Source-IP trust stays off until D5

`TRUSTED_PROXY_HOPS=0` and `TRUST_CLIENT_IP_HEADER=false` are the defaults. With trust off the browser's address is not known; FastAPI
throttles by the BFF's address (the S1 trade-off). The mechanism exists and is tested; **D5 must determine the real number of trusted proxy
hops from the real Coolify/proxy chain and then set both values together.** Nothing is guessed here.

## D5 verification items

Everything below depends on the real platform and is **not verified**:

* how Coolify provisions the managed PostgreSQL 17 and which administrator role the bootstrap step may use; whether the `pg_read_all_data`
  backup role can be created there;
* how a one-shot job (migration, backup, `admin`) is run in Coolify, and whether it can be ordered "backup, then migrate only on success";
* which health probe Coolify uses for deployment gating versus restarts (readiness versus liveness);
* container replacement semantics (old container removed before or after the new one is healthy);
* whether a command's output (the first-operator setup link) is persisted by the platform's terminal or job logs;
* the number of trusted proxy hops, and that the proxy does not add a second HSTS header;
* where backups are stored off-host, the credential for that location, and that the application runtime cannot delete backup history;
* the measured restore time on the real database size, recorded in the drill log (backup-restore.md).
