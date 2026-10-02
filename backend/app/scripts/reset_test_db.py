"""Reset the dedicated TEST database: drop everything, migrate to head, optionally seed.

    python -m app.scripts.reset_test_db          # empty, migrated schema (what pytest uses)
    python -m app.scripts.reset_test_db --seed   # plus the dev seed (what Playwright uses)

Automated tests (pytest, Playwright) run against a SEPARATE Postgres server
(`docker compose up -d postgres-test`, port 5433) that is configured only through
TEST_DATABASE_URL. This module is the single guard: it refuses to touch a database whose
name does not end in `_test`, or that is the development database. The development
database is never read or written by tests.

Imports of the application are lazy on purpose: the target URL must be exported as
DATABASE_URL before `app.core.config` is first imported.
"""

import argparse
import os
import sys
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

BACKEND_DIR = Path(__file__).resolve().parents[2]
ROOT_DIR = BACKEND_DIR.parent
TEST_DB_SUFFIX = "_test"


class UnsafeDatabase(RuntimeError):
    """Refusing to run destructive test setup against this database."""


def _environment() -> dict[str, str | None]:
    return {**dotenv_values(ROOT_DIR / ".env"), **os.environ}


def test_database_url(env: dict[str, str | None] | None = None) -> str:
    url = (env or _environment()).get("TEST_DATABASE_URL")
    if not url:
        raise UnsafeDatabase(
            "TEST_DATABASE_URL is not set. Tests never fall back to the development database: "
            "set it in .env (see .env.example) and start the test server with "
            "`docker compose up -d postgres-test`."
        )
    return url


def _development_urls() -> tuple[str, ...]:
    """The development database URL(s), captured ONCE when this module is first imported,
    before anything exports the test URL as DATABASE_URL."""
    test_url = _environment().get("TEST_DATABASE_URL")
    candidates = {dotenv_values(ROOT_DIR / ".env").get("DATABASE_URL"), os.environ.get("DATABASE_URL")}
    return tuple(sorted(url for url in candidates if url and url != test_url))


DEVELOPMENT_DATABASE_URLS = _development_urls()


def assert_is_test_database(url: str, dev_urls: tuple[str, ...] | None = None) -> None:
    """Raise unless `url` is clearly a test database and not a development one."""
    target = make_url(url)
    if not (target.database or "").endswith(TEST_DB_SUFFIX):
        raise UnsafeDatabase(
            f"Refusing to use database {target.database!r}: a test database name must end "
            f"with {TEST_DB_SUFFIX!r}."
        )
    for dev_url in DEVELOPMENT_DATABASE_URLS if dev_urls is None else dev_urls:
        dev = make_url(dev_url)
        if dev.database == target.database:
            raise UnsafeDatabase("The test database must not be the development database.")
        if (dev.host or "localhost", dev.port or 5432) == (target.host or "localhost", target.port or 5432):
            raise UnsafeDatabase(
                "The test database must live on a different server than the development database "
                "(use the postgres-test service, port 5433): they must be physically separate."
            )


def reset_schema(url: str) -> None:
    assert_is_test_database(url)
    engine = create_engine(url, isolation_level="AUTOCOMMIT")
    try:
        with engine.connect() as connection:
            connection.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
    finally:
        engine.dispose()


def migrate(url: str) -> None:
    assert_is_test_database(url)
    os.environ["DATABASE_URL"] = url
    from alembic import command
    from alembic.config import Config

    from app.core.config import settings

    if settings.database_url != url:  # the application was already configured for another database
        raise UnsafeDatabase("The application is configured for a different database than the one being reset.")
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    command.upgrade(config, "head")


def seed(url: str) -> None:
    assert_is_test_database(url)
    os.environ["DATABASE_URL"] = url
    from app.core.config import settings
    from app.core.db import SessionLocal
    from app.scripts.seed_dev import seed as seed_development_data

    if settings.database_url != url:
        raise UnsafeDatabase("The application is configured for a different database than the one being seeded.")
    with SessionLocal() as db:
        seed_development_data(db)
        db.commit()


def reset(url: str, *, with_seed: bool = False) -> None:
    reset_schema(url)
    migrate(url)
    if with_seed:
        seed(url)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seed", action="store_true", help="also load the development seed data")
    args = parser.parse_args(argv)
    try:
        url = test_database_url()
        assert_is_test_database(url)
        os.environ["DATABASE_URL"] = url
        reset(url, with_seed=args.seed)
    except UnsafeDatabase as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    print(f"test database {make_url(url).database!r} reset" + (" and seeded" if args.seed else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
