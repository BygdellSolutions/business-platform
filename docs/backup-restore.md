# Backup, restore and disaster recovery

**The rule: a backup is not verified until it has been restored into a separate, isolated database and the restored state has been
checked.** A file that exists, has a size and a hash is a *candidate*. This document describes the tools that make and prove a backup,
what they guarantee, what they do not, and what an operator must do around them.

Written in D4. Tools and tests exist and were run against disposable databases; **nothing is deployed and nothing here has been run
against a production database.** What depends on the real platform (Coolify, the off-host store) is marked *D5* and is not claimed.
Deployment context: [deployment.md](deployment.md). Design background: [architecture.md](architecture.md), "Backup and restore (D4 as built)".

## 1. What is protected, and how much can be lost

* **Method:** nightly *logical* backups of the **whole database** with `pg_dump --format=custom` (PostgreSQL 17 client). The dump holds
  every table, including authentication records (users, credentials, sessions, setup tokens, invitations, security events), the
  tenant data, and the frozen invoice PDFs (`invoice_pdfs.content`, `bytea`), plus triggers, functions, constraints, indexes, sequences
  and the `alembic_version` row. Nothing is selected or excluded.
* **Recovery point objective (RPO): up to 24 hours of data.** Anything written after the last nightly backup is lost in a disaster. This
  is an **accepted limitation** of the first deployment, not a goal. Later upgrade paths, **not implemented**: more frequent dumps, WAL
  archiving / point-in-time recovery (PITR), or a managed provider's backups.
* **Recovery time objective (RTO): none promised.** Section 9 records a *measured baseline* from a drill; it is a measurement, not a
  commitment, and it was taken on a tiny synthetic database.
* PDFs stay in PostgreSQL (so one backup covers everything). Section 11 gives the signals for revisiting that.

## 2. The commands

All tools are `python -m app.scripts.<tool>` in the backend image (which carries the PostgreSQL 17 client programs). Each takes its
database from **one explicitly named variable and never falls back to `DATABASE_URL`, `.env` or any other source**. The password reaches
`pg_dump`/`pg_restore` only through `PGPASSWORD` in their environment, never on a command line. Output is one JSON log line per event
and never contains a password, URL, token, hash or tenant data; the clients' error text is scrubbed before it is shown.

### `backup`

```text
BACKUP_DATABASE_URL=postgresql://<backup role>:...@<host>/<database> \
python -m app.scripts.backup --output /backups/bp-<label>-<UTC timestamp>.dump --label <label> [--git-revision <sha>]
```

* `--label`: 1 to 40 characters of letters, digits, `.`, `_`, `-`; a non-sensitive environment/purpose name (`production`, `pre-migrate`).
  `--git-revision` (or `APP_GIT_REVISION`) is recorded if given.
* **Never overwrites:** refuses (exit 2) if the dump, its manifest or either partial file already exists.
* **Consistent:** one `REPEATABLE READ` read-only transaction exports a snapshot; the manifest's counts and revision and `pg_dump
  --snapshot=...` describe the same instant, even while the application writes.
* **Atomic:** the dump is written to `<output>.partial`, checked readable (`pg_restore --list`), hashed, and moved into place with a
  hard link that cannot replace a file; then the manifest the same way. **Any failure removes every file this run created**; a failed
  run leaves no final backup. Exit codes: `0` done, `1` failed, `2` refused (configuration, an existing output, a client older than the server).
* Uploads nothing: copying off-host is section 6.

Use a **read-only backup role** (`BACKUP_ROLE`/`BACKUP_PASSWORD` in `bootstrap_roles`: membership of `pg_read_all_data`, `CONNECT`, owns
nothing). The owner or app role also works, but a backup does not need to be able to write.

### `restore`

```text
RESTORE_DATABASE_URL=postgresql://<owner role of THIS environment>:...@<host>/<name>_restore[_<tag>] \
RUNTIME_DB_ROLE=<the application role of THIS environment> \
python -m app.scripts.restore --dump /backups/bp-....dump [--manifest <dump>.manifest.json] [--reset-target] [--skip-runtime-grants]
```

Restores **only into a separate, scratch database.** It is not an in-place restore (section 8). Every guard refuses with exit 2 *before
anything is changed*:

| guard | rule |
|---|---|
| explicit target | `RESTORE_DATABASE_URL` only; no fallback to any other variable |
| scratch name | the database name must match `^[a-z][a-z0-9_]*_restore(_[a-z0-9]+)?$` (e.g. `bp_restore`, `bp_20261006_restore_a1`), **whatever the host**: a production-looking or development name is refused |
| not the source | the target must not be the database named in the manifest, nor the database (same host, port and name, whatever the credentials) of `DATABASE_URL`, `MIGRATION_DATABASE_URL` or `BACKUP_DATABASE_URL` if those are set |
| the dump is intact | the dump's size and SHA-256 must match its manifest (a truncated, extended or edited file is refused) |
| the right role | the connecting role must **own the target database**, must not be a superuser, and must not be `RUNTIME_DB_ROLE` |
| the runtime role exists | `RUNTIME_DB_ROLE` is required (and must exist) unless `--skip-runtime-grants` |
| empty target | a target holding tables, sequences or functions is refused unless `--reset-target`, which drops and recreates **only the target's `public` schema** |

Then it runs `pg_restore --no-owner --no-acl --single-transaction --exit-on-error`: **a failed restore is non-zero (exit 1) and the single
transaction leaves the target empty.** It never connects to, drops or modifies the source. Afterwards it reconciles the role grants
(section 4) and runs `ANALYZE`. The log line `restore_succeeded` carries `seconds`; record it (section 9).

### `verify_restore`

```text
VERIFY_DATABASE_URL=postgresql://<any role that can read>:...@<host>/<restored database> \
[VERIFY_APP_DATABASE_URL=postgresql://<the runtime application role>:...@<host>/<restored database>] \
python -m app.scripts.verify_restore [--manifest <dump>.manifest.json] [--alembic-check] [--fingerprint]
```

**Read-only** (its connection is `read_only` with `default_transaction_read_only`; it works as the read-only backup role). It prints one
JSON report and exits `0` only if every check passed (`1` otherwise, `2` for a configuration refusal). It prints counts and the names
of database objects, never data, emails, hashes, tokens or credentials.

| check | what it proves |
|---|---|
| `schema.single_revision`, `schema.exact_head` | `alembic_version` exists with exactly one row equal to this image's single head |
| `schema.alembic_check` (`--alembic-check`) | no drift between the models and the restored schema |
| `objects.triggers`, `objects.functions` | the guarantees the schema rests on exist and are enabled: last-owner backstop (deferred constraint triggers), append-only security events, invoice and invoice-child immutability, the PDF guard, the currency lock |
| `objects.constraints_validated` | every constraint (foreign keys, CHECKs) is validated |
| `objects.tenant_columns` | every tenant table still has a NOT NULL `organization_id` |
| `data.counts_match_manifest` (`--manifest`), `data.tenant_core_present` | the row counts equal the backup's; organizations, users and memberships exist |
| `pdf.integrity` | for **every** stored PDF: bytes exist, `byte_size` equals their length, SHA-256 equals the stored `sha256`, and they start with `%PDF-` |
| `auth.coherent` | credentials have Argon2id hashes and a user; sessions, setup tokens and invitations have well-formed hash columns and a user; no invitation is both accepted and revoked |
| `roles.runtime_role` (`VERIFY_APP_DATABASE_URL`) | the runtime role is not a superuser/creator, owns nothing, has DML on every table and sequence, is read-only on `alembic_version`, cannot `TRUNCATE` or `CREATE`, and a real `CREATE TABLE` is **denied** (rolled back whatever happens) |

`--fingerprint` adds a content fingerprint (per-table counts and a hash over every row, sequence positions). A database and its faithful
restore have the **same** fingerprint, so "the restore equals the source" and "the source did not change" are comparable facts.

The schema lists in the verifier are kept honest by a test that fails when the migrated schema gains a trigger, function or tenant
table the verifier does not name.

### `db_stats`

```text
STATS_DATABASE_URL=postgresql://<any role that can read>:...@<host>/<database> python -m app.scripts.db_stats
```

Read-only; prints database bytes, the PDF table's row count, **total stored PDF bytes**, its on-disk size, the five largest tables and
the review thresholds (section 11).

## 3. The manifest

Next to every dump, `<dump>.manifest.json` (format `bp-backup/1`) is written. It contains **no connection string, password, token,
authentication material or tenant data**, and is not signed (integrity of the pair rests on the SHA-256 and on where you store it).

| field | meaning |
|---|---|
| `format` | `bp-backup/1` |
| `created_at_utc` | when the backup finished |
| `label`, `git_revision` | the label given; the application revision if supplied (else `null`) |
| `source.database` | the source database *name* (not host, not credentials) |
| `alembic_revisions`, `code_alembic_heads` | the database's revision(s) at the snapshot, and the head of the image that made the backup |
| `postgres_server_version`, `pg_dump_major_version` | versions |
| `dump_options`, `toc_entries` | how the dump was made; how many objects the archive lists |
| `dump.file`, `dump.bytes`, `dump.sha256` | the dump's name, size and SHA-256 (the restore tool checks all three) |
| `database_bytes`, `invoice_pdfs.count`, `invoice_pdfs.bytes` | sizes at the snapshot |
| `table_counts` | row count per table at the snapshot (used by `verify_restore --manifest`) |
| `duration_seconds` | backup duration, as measured by the tool |

Always store the manifest **with** its dump. A dump whose manifest is lost can still be restored by `pg_restore` by hand, but the tool
refuses it, deliberately.

## 4. Roles and ownership (the one model)

**The dump contains no owner and no privileges** (`--no-owner --no-acl`), and no role or password. Restoring therefore behaves the same
whether the recovery environment's role names equal the source's or differ.

1. The recovery environment has its own **owner role** and **runtime (app) role** (create them with `bootstrap_roles`; they may or may not
   have the same names as in production).
2. `restore` connects **as the owner role** (never a superuser, never the runtime role), so every restored object is owned by it.
3. `restore` then runs the **same grant reconciliation the migration job runs**: the runtime role gets `SELECT/INSERT/UPDATE/DELETE` on
   tables and `USAGE/SELECT` on sequences, the owner's default privileges are set, and write access to `alembic_version` is revoked.
4. The runtime role is **never** made a superuser, owner or DDL-capable to make a restore work. The result is a database where the
   application can do DML and cannot do DDL, which `verify_restore` proves with the runtime role's own credentials.

Preparing the target: create an empty database named `..._restore[_tag]`, owned by the owner role, with `CONNECT` for the runtime role:

```text
# a privileged connection to the recovery server, once per scratch database
CREATE DATABASE bp_restore_20261006;
BOOTSTRAP_DATABASE_URL=postgresql://<admin>:...@<host>/bp_restore_20261006 OWNER_ROLE=... OWNER_PASSWORD=... APP_ROLE=... APP_PASSWORD=... \
python -m app.scripts.bootstrap_roles
```

`bootstrap_roles` is idempotent and **resets the passwords of roles that already exist** to the ones you give: on a server that already
hosts the production roles, pass their *current* passwords. No production password is stored in a dump or in this repository; passwords
come from the platform's secret store.

## 5. Pre-migration backup (a contract, not wiring)

Before a migration of a database that holds data: run `backup` (label e.g. `pre-migrate`), require **exit 0 and a manifest**, and only then
run the migration job. The migration runner does **not** back up silently and is not changed; the orchestration belongs to the deployment
procedure and is proved in D5 ([deployment.md](deployment.md), section 6). A first deployment into an empty database needs none.

## 6. Storage: the off-host contract (documented, not implemented)

The tools write to a local path. For production the backup set must leave the database host:

* **Location:** a storage location **outside the database host and its volume** (an S3-compatible bucket or equivalent). A backup stored
  only on the machine that holds the database is not a backup of that machine.
* **In transit:** TLS. **At rest:** provider-side encryption at minimum; **client-side encryption (before upload) preferred**, so the
  provider never holds readable data. The dump contains password hashes, session hashes and every tenant's data: treat it as the most
  sensitive object the system produces.
* **Keys and credentials:** **no key and no credential is in this repository, an image or a CI secret.** The upload credential is a
  *separate, least-privilege backup credential* (write new objects, list; not delete, not read other systems), distinct from the
  database roles and from the application's.
* **The application runtime cannot delete backup history.** Nothing the web process holds may be able to remove or overwrite a backup
  (use object versioning/lock or a write-only credential).
* **Verification:** a copy is not "stored" until its SHA-256 matches the manifest after upload.

D5 decides the concrete store and proves the credential model. **Nothing in D4 uploads anything.**

## 7. Retention

Keep **14 daily, 8 weekly and 6 monthly** backups (with their manifests). Pruning is performed by the storage location's lifecycle rules,
never by the application. A `pre-migrate` backup is kept at least until the release it protects has run through one full weekly cycle.
The first restore drill (section 9) must happen **before** relying on any of this.

## 8. Disaster recovery outline

Never a destructive in-place restore over the live database as the normal path. Recover into a **new, separate** database, prove it, then switch.

1. **Decide and contain.** Name the incident. If the cause is a compromise rather than a failure, decide first what a restore brings back:
   it restores the sessions and credentials of the backup moment. (There is no bulk session-revocation command yet: tracked in TODO.md.)
2. **Choose the backup:** the newest whose manifest and `verify_restore` result you trust; fetch dump and manifest from the off-host store
   and confirm the SHA-256 (the restore tool refuses a mismatch anyway).
3. **Provision** a new database (new server, or a new database on a healthy server) named `..._restore[_tag]`, with the recovery environment's owner and
   runtime roles (section 4).
4. **Restore** with `restore`; a failure leaves the target empty, so you may retry on it.
5. **Verify** with `verify_restore --manifest ... --alembic-check` and `VERIFY_APP_DATABASE_URL`. A failed check means **do not switch**.
6. **Rehearse the application:** start the backend image against the restored database as the runtime role; require `/health/ready`, a
   sign-in/session lookup, a tenant read and an invoice PDF download.
7. **Switch:** point the backend's `DATABASE_URL` (and the migration job's `MIGRATION_DATABASE_URL`) at the restored database, redeploy,
   confirm readiness. The previous database stays **untouched** until the new one has served real traffic.
8. **Account for the loss:** data after the backup's `created_at_utc` (up to the RPO) must be re-entered or reconstructed; tell the affected people.
9. **Afterwards:** take a fresh backup of the recovered database and record the incident and the timings. The recovered database may keep its
   `..._restore_...` name (nothing depends on the name); renaming it needs a moment with no connections, so do it in a planned maintenance window.

For rollback of a *release* (as opposed to data), see [deployment.md](deployment.md), section 10: fix forward; no automatic downgrade.

## 9. The drill

The complete rehearsal is automated: `cd backend && uv run pytest ../deploy/drill -q -s` (Docker required; disposable containers only,
no development/production database can be reached). It runs, in the real production backend image:

1. create the source database; 2. migrate it with the real migration job (owner role, grants); 3. fill it **through the application** as
the restricted runtime role (two organizations with identical customer names, every role, one person in both organizations,
credentials, a live and a revoked session, a setup token, invitations pending/accepted/revoked, customers, items, a horse, custom fields,
a completed transaction, an **issued invoice with a frozen PDF** in each organization, security events); 4. fingerprint the source;
5. **back up** as the read-only role (real `pg_dump`); 6. create a **separate** restore database in a **separate** server with
**different role names**; 7. **restore**; 8. `verify_restore --manifest --alembic-check`; 9. fingerprint the restore (**equal to the
source's**); 10. start the real backend image against the restored database as the restricted role: `/health/ready`, a session created
before the backup, two memberships, a tenant read, an issued-invoice read, a **foreign invoice id = 404**, and the **PDF downloaded
byte-for-byte equal (SHA-256) to the frozen original**; 11. confirm the source fingerprint is unchanged; 12. a **damaged dump with a
manifest that agrees with it** fails in `pg_restore` (exit 1) and leaves the target empty; a restore onto a non-empty target is refused;
13. destroy all containers, the volume, the network and the images.

It also runs weekly and on demand in GitHub Actions (`restore-drill.yml`), **never as a merge gate** and never with a real backup, credential
or database. The tool-logic and guard tests (fake PostgreSQL clients) and the verifier tests run in the ordinary backend job.

### Drill log

Record every real drill here. How to record: take the numbers from the commands themselves; **backup**: the manifest's `dump.bytes`,
`database_bytes`, `invoice_pdfs.bytes`, `duration_seconds`; **restore**: `seconds` in the `restore_succeeded` log line (or the wall time of the
command); **verify**: the report's `ok`. Add one row per drill. Record the *environment* (a restore on production-sized data on the real
server is the only measurement that means anything for recovery planning).

| date (UTC) | environment | database | dump | PDFs | backup | restore | verify | result |
|---|---|---|---|---|---|---|---|---|
| 2026-10-06 | disposable Docker containers on a developer machine (PostgreSQL 17.11) | 10.2 MB | 0.21 MB | 2 (90 kB) | 0.18 s in the tool, 1.1 s as a container | 1.2 s as a container | 2.1 s (with `alembic check`) | **pass**; restored fingerprint equals source; PDF SHA-256 equal; damaged dump failed in `pg_restore`; source unchanged |

**This is a baseline, not an RTO.** A 200 kB dump of a synthetic database says nothing about production restore time: PDF-heavy data,
indexes and the real server's hardware dominate. The first drill on the real platform (D5) replaces it.

## 10. Failure behaviour you can rely on

* `backup` failing in any way leaves **no** dump, manifest or partial file, exits non-zero and prints no credential.
* `restore` refusing exits 2 having changed nothing; `restore` failing in `pg_restore` exits 1 and leaves the target empty.
* `verify_restore` failing exits 1; **a database that fails verification is not used.**
* A log, manifest or report never contains a password, connection string, token, hash or tenant data (tested with sentinel values).

## 11. Measuring, and when to revisit "PDFs in PostgreSQL"

`python -m app.scripts.db_stats` (section 2) reports the numbers; each backup's manifest records size and duration; each drill records restore
duration. **Review signals** (a prompt to review the design, not an error): **total stored PDF bytes of about 2 GB**, or a **backup taking about
15 minutes**. Possible responses then (none implemented): moving PDFs to object storage, more frequent or incremental backups, WAL/PITR.

## 12. CI boundary

* **No production backup ever runs in GitHub Actions**, and no production database URL, backup credential or backup is a repository secret.
* CI runs the tool tests against disposable databases and fake clients (backend job) and the real drill in its own manual/weekly workflow
  (`restore-drill.yml`, not a gate).

## 13. What D4 does not do

No upload to storage; no scheduler (the nightly schedule is a deployment task, D5); no encryption implementation; no WAL/PITR; no
automatic pre-migration backup; no automatic downgrade; no PDF move to object storage; no change to application behaviour.
