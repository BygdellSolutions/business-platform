"""Create the two database roles of the first-deployment model, once per database, as a privileged role.

    BOOTSTRAP_DATABASE_URL=postgresql://<admin>:...@host/<db> \\
    OWNER_ROLE=bp_owner OWNER_PASSWORD=... APP_ROLE=bp_app APP_PASSWORD=... \\
    python -m app.scripts.bootstrap_roles

The model (see docs/architecture.md, "Database roles"):

  owner role (MIGRATION_DATABASE_URL)  owns the database and its schema; runs migrations, and so owns every table.
  app role   (DATABASE_URL)            what FastAPI runs as: DML on tables and sequences, and nothing else. No superuser, no
                                       CREATEDB, no CREATEROLE, no CREATE on the schema, no ownership: it cannot create,
                                       alter or drop anything.

Objects created by FUTURE migrations are usable by the app role without a new grant, because the owner's default
privileges (set here, and again by every migration job) cover them. This script is the only place that holds a
privileged connection, it is not part of the web process, and it is idempotent (roles are created or have their
password reset, grants are re-applied).
"""

import os
import sys

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

ROLE_ATTRIBUTES = "LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"


def _ensure_role(cursor, name: str, password: str) -> None:
    cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (name,))
    verb = "ALTER" if cursor.fetchone() else "CREATE"
    cursor.execute(
        sql.SQL("{} ROLE {} WITH " + ROLE_ATTRIBUTES + " PASSWORD {}").format(sql.SQL(verb), sql.Identifier(name), sql.Literal(password))
    )


def bootstrap(admin_url: str, *, owner_role: str, owner_password: str, app_role: str, app_password: str) -> None:
    """Idempotent. `admin_url` connects to the target database as a role allowed to create roles and change ownership."""
    owner, app = sql.Identifier(owner_role), sql.Identifier(app_role)
    with psycopg.connect(admin_url, autocommit=True) as connection:
        database = sql.Identifier(conninfo_to_dict(admin_url)["dbname"])
        with connection.cursor() as cursor:
            _ensure_role(cursor, owner_role, owner_password)
            _ensure_role(cursor, app_role, app_password)
            cursor.execute(sql.SQL("ALTER DATABASE {} OWNER TO {}").format(database, owner))
            cursor.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(database))
            cursor.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(database, app))
            cursor.execute(sql.SQL("ALTER SCHEMA public OWNER TO {}").format(owner))
            cursor.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
            cursor.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(app))
            for statement in (
                "ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {app}",
                "ALTER DEFAULT PRIVILEGES FOR ROLE {owner} IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO {app}",
            ):
                cursor.execute(sql.SQL(statement).format(owner=owner, app=app))
            # Objects that already exist (a database that was migrated before the roles were introduced) move to the owner.
            cursor.execute(
                sql.SQL(
                    "DO $$ DECLARE r record; BEGIN "
                    "FOR r IN SELECT format('%I.%I', schemaname, tablename) AS name FROM pg_tables WHERE schemaname = 'public' LOOP "
                    "EXECUTE format('ALTER TABLE %s OWNER TO %I', r.name, {owner_name}); END LOOP; "
                    "FOR r IN SELECT format('%I.%I', sequence_schema, sequence_name) AS name FROM information_schema.sequences WHERE sequence_schema = 'public' LOOP "
                    "EXECUTE format('ALTER SEQUENCE %s OWNER TO %I', r.name, {owner_name}); END LOOP; END $$"
                ).format(owner_name=sql.Literal(owner_role))
            )


def main() -> int:
    try:
        bootstrap(
            os.environ["BOOTSTRAP_DATABASE_URL"],
            owner_role=os.environ["OWNER_ROLE"],
            owner_password=os.environ["OWNER_PASSWORD"],
            app_role=os.environ["APP_ROLE"],
            app_password=os.environ["APP_PASSWORD"],
        )
    except KeyError as missing:
        print(f"missing environment variable {missing}", file=sys.stderr)
        return 2
    except psycopg.Error as error:
        print(f"bootstrap failed: {type(error).__name__}", file=sys.stderr)  # never the message: it can quote the URL
        return 1
    print("roles ready")
    return 0


if __name__ == "__main__":
    sys.exit(main())
