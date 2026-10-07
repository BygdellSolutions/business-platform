# Coolify deployment definitions (D5 staging rehearsal)

Under review on the `d5-staging` branch. One Coolify **Docker Compose** resource per file, built from this repository.

## Why one resource per process

Observed in D5 (Coolify 4.3.23): a Compose resource has ONE set of environment variables, and Coolify gives every service in
it all of them (it adds `env_file: .env` to each service). In a single stack the frontend received `DATABASE_URL`,
`MIGRATION_DATABASE_URL` and `SECURITY_KEY`, and the backend received `MIGRATION_DATABASE_URL`, which it refuses at startup
(D2 fail-closed rule) and then crash-looped because Coolify adds `restart: unless-stopped`. So each credential boundary is its
own resource, with only its own variables:

| file | resource holds | public |
|---|---|---|
| `bootstrap.yml` | database superuser URL, role names and passwords | no; deploy ONCE per database, then delete the resource |
| `migrate.yml` | `MIGRATION_DATABASE_URL`, `RUNTIME_DB_ROLE` | no |
| `backend.yml` | `DATABASE_URL`, `SECURITY_KEY`, `BFF_INTERNAL_SECRET`, `PUBLIC_ORIGIN`, `TRUST_CLIENT_IP_HEADER` | no |
| `frontend.yml` | `PUBLIC_ORIGIN`, `BACKEND_URL`, `BFF_INTERNAL_SECRET`, `TRUSTED_PROXY_HOPS` | yes (the only public service) |

## Rules observed in Coolify

* **Paths:** Coolify runs Compose with the REPOSITORY ROOT as the project directory, so build contexts are `./backend` and
  `./frontend` (relative to the repository root, not to the file).
* **Every variable must be runtime-only.** Coolify passes build-time variables to the image build as `--build-arg`; the images
  need no build-time configuration (D1).
* **"Finished" means "containers started".** Coolify does not report a one-shot container's exit code, and its API cannot read
  a stopped container's logs. Each one-shot therefore has a `*-gate` service that `depends_on` it (or the backend's health), so
  `docker compose up` itself fails, and Coolify marks the DEPLOYMENT failed, when a migration fails or the backend never
  becomes ready.

## Deployment order (cross-resource; Coolify cannot express it)

1. backup (D4) when the database holds data; it must succeed;
2. `migrate` resource: its deployment must finish successfully (the gate makes a failed migration a failed deployment);
3. `backend` resource: its deployment finishes only when the backend is READY (exact Alembic head);
4. `frontend` resource.

Exact-head readiness is the backstop if the order is broken: a backend whose database is not at its head never becomes ready.
**Frontend -> backend:** Coolify drops Compose network aliases and its own `custom_network_aliases` for Compose resources, and
by default names containers with a per-deployment suffix (all observed). The backend resource therefore has "Consistent Container
Names" enabled, which names the container `backend-<resource uuid>`, and the frontend's `BACKEND_URL` is
`http://backend-<resource uuid>:8000` (a single-label name, as the BFF requires). Both live on Coolify's shared `coolify` network.
