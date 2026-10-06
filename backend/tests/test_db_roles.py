"""Database role separation: an OWNER role (migrations) and an APP role (the web process, DML only).

  * the runtime role is not a superuser, cannot create databases or roles, and cannot create, alter or drop anything,
    nor change the revision table;
  * the migration role can migrate, and what migrations create (now and in the future) is usable by the runtime role
    without a new grant;
  * the ordinary application suite passes connected as the runtime role.

All of it on disposable databases and roles of the TEST server.
"""

import os
import subprocess
import sys
import uuid
from types import SimpleNamespace

import psycopg
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import DBAPIError

from app.scripts.bootstrap_roles import bootstrap
from tests.db_support import BACKEND_DIR, admin_url, disposable_database, disposable_roles, psycopg_url, run_migrate, scalar, url_for
from tests.test_migrate_runner import script_dir
from app.core import migration

HEAD = migration.code_heads()[0]


def role_url(database_url: str, role: str, password: str) -> str:
    from sqlalchemy.engine import make_url

    return make_url(database_url).set(username=role, password=password).render_as_string(hide_password=False)


def make_world(admin: str, roles: dict):
    owner, app = roles["names"]["owner"], roles["names"]["app"]
    bootstrap(psycopg_url(admin), owner_role=owner, owner_password=roles["passwords"]["owner"], app_role=app, app_password=roles["passwords"]["app"])
    return SimpleNamespace(
        admin=admin, owner=owner, app=app,
        owner_url=role_url(admin, owner, roles["passwords"]["owner"]), app_url=role_url(admin, app, roles["passwords"]["app"]),
    )


@pytest.fixture(scope="module")
def world():
    with disposable_roles("owner", "app") as roles, disposable_database("roles") as admin:  # (the database goes first: it is owned by a role)
        w = make_world(admin, roles)
        result = run_migrate(w.owner_url, env={"APP_ENV": "production", "RUNTIME_DB_ROLE": w.app})
        assert result.returncode == 0, result.stdout + result.stderr
        yield w


def app_engine(w):
    return create_engine(w.app_url, hide_parameters=True)


def denied(w, statement: str) -> bool:
    engine = create_engine(w.app_url, hide_parameters=True, isolation_level="AUTOCOMMIT")  # (CREATE DATABASE refuses a transaction block)
    try:
        with engine.connect() as connection:
            connection.execute(text(statement))
        return False
    except DBAPIError as error:
        return "permission denied" in str(error.orig) or "must be owner" in str(error.orig) or "must have" in str(error.orig)
    finally:
        engine.dispose()


# --- who the roles are ---------------------------------------------------------------------------------------------------------------------------


def test_the_runtime_role_has_no_dangerous_attribute_and_does_not_own_anything(world):
    flags = scalar(
        world.admin,
        "select rolsuper::text || rolcreatedb::text || rolcreaterole::text || rolreplication::text || rolbypassrls::text from pg_roles where rolname = :r",
        r=world.app,
    )
    assert flags == "falsefalsefalsefalsefalse"
    assert scalar(world.admin, "select count(*) from pg_class c join pg_roles r on r.oid = c.relowner where r.rolname = :r", r=world.app) == 0
    assert scalar(world.admin, "select pg_has_role(:a, :o, 'member')", a=world.app, o=world.owner) is False  # not the owner by membership either
    assert scalar(world.admin, "select r.rolname from pg_database d join pg_roles r on r.oid = d.datdba where d.datname = current_database()") == world.owner


def test_the_owner_role_is_not_a_superuser_either(world):
    assert scalar(world.admin, "select rolsuper or rolcreaterole or rolcreatedb from pg_roles where rolname = :r", r=world.owner) is False


def test_the_migration_role_migrated_everything_and_owns_it(world):
    assert scalar(world.admin, "select version_num from alembic_version") == HEAD
    assert scalar(world.admin, "select count(*) from pg_tables where schemaname = 'public' and tableowner <> :o", o=world.owner) == 0


# --- what the runtime role cannot do -----------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "statement",
    [
        "create table evil (id int)",
        "alter table organizations add column evil text",
        "alter table organizations rename to organizations_gone",
        "drop table customers",
        "drop table alembic_version",
        "truncate table customers",
        "create role evil_role",
        "create database evil_db",
        "create schema evil_schema",
        "drop schema public cascade",
        "alter table customers disable trigger all",
        "create index evil_index on customers (name)",
        "create function evil() returns int language sql as 'select 1'",
        "update alembic_version set version_num = 'ffffffffffff'",
        "delete from alembic_version",
        "insert into alembic_version (version_num) values ('ffffffffffff')",
        "set session_replication_role = replica",
    ],
)
def test_the_runtime_role_cannot_change_the_schema_or_the_revision(world, statement):
    assert denied(world, statement), statement


def test_the_runtime_role_cannot_grant_privileges_to_anyone(world):
    engine = create_engine(world.app_url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.execute(text("grant all on customers to public"))  # PostgreSQL only WARNS: it has no grant option
    finally:
        engine.dispose()
    assert scalar(world.admin, "select count(*) from pg_class c, unnest(c.relacl) a where c.relname = 'customers' and a::text like '=%'") == 0


def test_the_runtime_role_can_read_the_revision_table_which_is_all_readiness_needs(world):
    engine = app_engine(world)
    try:
        with engine.connect() as connection:
            assert connection.execute(text("select version_num from alembic_version")).scalar() == HEAD
    finally:
        engine.dispose()
    assert scalar(world.admin, "select version_num from alembic_version") == HEAD  # and the denied writes above changed nothing


# --- what it can do ----------------------------------------------------------------------------------------------------------------------------


def test_the_runtime_role_can_do_ordinary_work_including_deferred_triggers_on_real_commits(world):
    org, user = uuid.uuid4(), uuid.uuid4()
    engine = app_engine(world)
    try:
        with engine.begin() as c:
            c.execute(text("insert into organizations (id, name, default_currency) values (:o, 'Roles AB', 'SEK')"), {"o": org})
            c.execute(text("insert into users (id, email, name) values (:u, :e, 'Roles')"), {"u": user, "e": f"roles-{user}@example.test"})
            c.execute(text("insert into organization_users (organization_id, user_id, role) values (:o, :u, 'owner')"), {"o": org, "u": user})
            c.execute(text("insert into customers (id, organization_id, name, customer_type) values (:i, :o, 'Anna', 'person')"), {"i": uuid.uuid4(), "o": org})
        with engine.begin() as c:
            c.execute(text("update customers set name = 'Anna A' where organization_id = :o"), {"o": org})
            assert c.execute(text("select count(*) from customers where organization_id = :o"), {"o": org}).scalar() == 1
        # the deferred owner-loss trigger runs as the CALLER at commit: removing the only owner must be refused
        with pytest.raises(DBAPIError, match="owner"):
            with engine.begin() as c:
                c.execute(text("delete from organization_users where organization_id = :o"), {"o": org})
        with engine.begin() as c:
            c.execute(text("delete from customers where organization_id = :o"), {"o": org})
    finally:
        engine.dispose()


def test_the_application_and_its_readiness_probe_run_as_the_runtime_role(world, monkeypatch):
    from app.core import db, readiness

    engine = create_engine(world.app_url, hide_parameters=True)
    try:
        assert readiness.check(engine) == readiness.READY
    finally:
        engine.dispose()


# --- objects created by FUTURE migrations ---------------------------------------------------------------------------------------------------------


NEW_OBJECTS = (
    'op.execute("create table future_things (id serial primary key, label text not null)"); '
    'op.execute("create sequence future_seq"); '
    'op.execute("create function future_fn() returns int language sql as \'select 41\'")'
)


def exercise_future_objects(w, table: str = "future_things", sequence: str = "future_seq") -> None:
    engine = app_engine(w)
    try:
        with engine.begin() as c:
            c.execute(text(f"insert into {table} (label) values ('x')"))  # needs INSERT and USAGE on the serial's sequence
            c.execute(text(f"update {table} set label = 'y'"))
            assert c.execute(text(f"select count(*) from {table}")).scalar() == 1
            assert c.execute(text(f"select nextval('{sequence}')")).scalar() >= 1
            assert c.execute(text("select future_fn()")).scalar() == 41
            c.execute(text(f"delete from {table}"))
    finally:
        engine.dispose()


def test_default_privileges_alone_cover_what_a_future_migration_creates(tmp_path):
    """A migration run BY HAND with plain Alembic (no job, no reconcile step) as the owner: only the bootstrap's default
    privileges can make its new objects usable."""
    from alembic import command

    with disposable_roles("owner", "app") as roles, disposable_database("roles") as admin:  # (the database goes first: it is owned by a role)
        w = make_world(admin, roles)
        directory = script_dir(tmp_path / "scripts", ("t0000000001", None, NEW_OBJECTS))
        config = migration.alembic_config(directory)
        config.attributes["database_url"] = w.owner_url
        command.upgrade(config, "head")
        exercise_future_objects(w)


def test_the_migration_job_alone_covers_a_database_whose_defaults_were_never_set(tmp_path):
    """No bootstrap default privileges at all (a database prepared by hand): the job's reconcile step makes the first
    migration's objects usable AND sets the defaults, so a second, later migration needs nothing either."""
    with disposable_roles("owner", "app") as roles, disposable_database("roles") as admin:  # (the database goes first: it is owned by a role)
        owner, app = roles["names"]["owner"], roles["names"]["app"]
        with psycopg.connect(psycopg_url(admin), autocommit=True) as c:
            for name in (owner, app):
                c.execute(f'create role "{name}" login password \'{roles["passwords"][ "owner" if name == owner else "app"]}\' nosuperuser nocreatedb nocreaterole')
            c.execute(f'alter database "{psycopg.conninfo.conninfo_to_dict(psycopg_url(admin))["dbname"]}" owner to "{owner}"')
            c.execute(f'alter schema public owner to "{owner}"')
        w = SimpleNamespace(admin=admin, owner=owner, app=app, owner_url=role_url(admin, owner, roles["passwords"]["owner"]), app_url=role_url(admin, app, roles["passwords"]["app"]))
        first = script_dir(tmp_path / "one", ("t0000000001", None, NEW_OBJECTS))
        result = run_migrate(w.owner_url, "--script-location", str(first), env={"APP_ENV": "production", "RUNTIME_DB_ROLE": app})
        assert result.returncode == 0, result.stdout + result.stderr
        exercise_future_objects(w)

        second = script_dir(
            tmp_path / "two",
            ("t0000000001", None, NEW_OBJECTS),
            ("t0000000002", "t0000000001", 'op.execute("create table later_things (id serial primary key, label text)")'),
        )
        # a migration by hand (no reconcile) now: the defaults the first job run installed carry it
        from alembic import command

        config = migration.alembic_config(second)
        config.attributes["database_url"] = w.owner_url
        command.upgrade(config, "head")
        exercise_future_objects(w, "later_things", "later_things_id_seq")


# --- the ordinary application suite, connected as the runtime role -----------------------------------------------------------------------------------


RUNTIME_ROLE_SUITE = [
    "tests/test_customers_api.py", "tests/test_items_api.py", "tests/test_transactions_api.py", "tests/test_horses_api.py",
    "tests/test_invoices_lifecycle.py", "tests/test_invoices_create.py", "tests/test_invoice_pdf_api.py",
    "tests/test_organization_creation.py", "tests/test_membership_admin.py", "tests/test_invitations.py",
    "tests/test_auth_login.py", "tests/test_auth_setup.py", "tests/test_auth_sessions.py", "tests/test_custom_field_values_api.py",
]


# These ARRANGE a state by switching triggers off (`alter table ... disable trigger`, `session_replication_role`), which
# only an owner or superuser may do: the test, not the application, needs that right.
OWNER_ONLY_ARRANGEMENTS = [
    "tests/test_invoices_lifecycle.py::test_issuing_fails_rather_than_issue_different_financial_content",
    "tests/test_invoice_pdf_api.py::test_the_first_download_after_every_live_record_changed_still_prints_the_issued_document",
    "tests/test_invoice_pdf_api.py::test_the_pdf_prints_the_stored_figures_not_the_ones_it_could_calculate",
    "tests/test_invoice_pdf_api.py::test_the_filename_is_sanitized_independently_of_the_invoice_content",
]


def test_the_ordinary_application_tests_pass_connected_as_the_runtime_role(world):
    """A child pytest on the same disposable database, with DATABASE_URL = the runtime role. (Tests that need DDL, the
    superuser or a different database - migrations, committed-data purges - are not in this list by nature.)"""
    # (the parent run exported DATABASE_URL = the test database; the child must see only the developer's .env as "the dev URL")
    environment = {k: v for k, v in os.environ.items() if k not in {"DATABASE_URL", "MIGRATION_DATABASE_URL"}}
    environment.update({"TEST_DATABASE_URL": world.admin, "TEST_RUNTIME_ROLE_URL": world.app_url})
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-x", *RUNTIME_ROLE_SUITE, *[f"--deselect={name}" for name in OWNER_ONLY_ARRANGEMENTS]],
        cwd=BACKEND_DIR, env=environment, capture_output=True, text=True, timeout=900,
    )
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-1500:]
    summary = result.stdout.strip().splitlines()[-1]
    assert " passed" in summary and "failed" not in summary and "error" not in summary
    assert int(summary.split(" passed")[0].split()[-1]) > 300  # it really ran the suite
