from logging.config import fileConfig

from alembic import context

from app.core import migration

# The migration job (app.scripts.migrate) runs with the MIGRATION credentials and no web configuration or secrets, and
# the model modules cannot be imported without the web process's settings (a module package imports its API). The
# migrations themselves are plain operations that never read the models, so `upgrade` (the job's, or the CLI's) loads no
# metadata. The metadata is needed only for `alembic revision --autogenerate` and `alembic check`, which are development
# and CI commands and therefore also need the web settings (DATABASE_URL, APP_ENV).

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    # disable_existing_loggers=False: running a migration inside a process that already configured logging (the test
    # reset does) must not silence the application's own loggers.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

def wants_metadata() -> bool:
    """Only autogenerate-style work reads the models. The migration job (it passes its connection) and the CLI's `upgrade`,
    `downgrade`, `current` ... never do, so they need the MIGRATION credentials and nothing of the web configuration."""
    options = config.cmd_opts
    if options is None:  # called programmatically
        return config.attributes.get("connection") is None
    return getattr(options.cmd[0], "__name__", "") in ("check", "revision")  # the command alembic is running


def load_target_metadata():
    if not wants_metadata():
        return None
    from app.core.base import Base
    from app import models  # noqa: F401  (registers models on Base.metadata)
    from app.modules.equine import models as equine_models  # noqa: F401  (domain module models)
    from app.modules.sales import models as sales_models  # noqa: F401  (Sales models)
    from app.modules.custom_fields import models as custom_field_models  # noqa: F401  (custom fields)
    from app.modules.invoicing import models as invoicing_models  # noqa: F401  (Invoicing models)

    return Base.metadata


target_metadata = load_target_metadata()


def database_url() -> str:
    """Where to migrate, in order: a URL the caller passed explicitly (the test helpers do, so a stray environment
    variable can never redirect a test run), then MIGRATION_DATABASE_URL, then (development only) DATABASE_URL."""
    explicit = config.attributes.get("database_url")
    if explicit:
        return explicit
    url = migration.MigrationSettings().url()
    if not url:
        raise RuntimeError("MIGRATION_DATABASE_URL is not set (DATABASE_URL is used only when APP_ENV=development)")
    return url


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode: emit SQL without a database connection."""
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    The migration job passes ONE connection (the one holding its advisory lock), so the lock and the migration share a
    session and a dropped connection fails the migration instead of silently dropping the lock. Otherwise an engine
    is created from `database_url()`.
    """
    connection = config.attributes.get("connection")
    if connection is not None:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
        return

    connectable = migration.migration_engine(database_url())
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
