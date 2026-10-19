"""the job queue: Procrastinate 3.10.0's schema inside our database, and the table that makes a job's effect happen once

The SQL is a vendored, frozen copy of the schema the pinned library version ships (alembic/sql/procrastinate_3_10_0.sql), so
this migration never changes when the library is upgraded; an upgrade is a NEW migration that applies the library's own
migration files. Jobs are written by `app.core.jobs.enqueue` through the library's stored function inside the caller's
database transaction.

`job_effects` is not business data: one row says "the effect of this job step has been applied" and is inserted in the same
transaction as the effect itself, so a job delivered twice (a crash after the effect, before the queue heard about it)
applies the effect once.

Revision ID: a1c3e5f7b9d2
Revises: f8b0d2e4a6c9
Create Date: 2026-10-15
"""

from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1c3e5f7b9d2"
down_revision: Union[str, Sequence[str], None] = "f8b0d2e4a6c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA = Path(__file__).resolve().parents[1] / "sql" / "procrastinate_3_10_0.sql"


def upgrade() -> None:
    # The driver connection, not exec_driver_sql: with no parameters psycopg runs the whole script as one simple query and
    # leaves the "%" characters of the SQL alone.
    op.get_bind().connection.driver_connection.execute(SCHEMA.read_text(encoding="utf-8"))
    op.create_table(
        "job_effects",
        sa.Column("key", sa.Text(), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("length(key) BETWEEN 1 AND 255", name="ck_job_effects_key_length"),
    )


def downgrade() -> None:
    op.drop_table("job_effects")
    op.execute("DROP TABLE procrastinate_events, procrastinate_periodic_defers, procrastinate_jobs, procrastinate_workers CASCADE")
    op.execute(
        r"""
        DO $$
        DECLARE f record;
        BEGIN
            FOR f IN SELECT oid::regprocedure AS signature FROM pg_proc
                     WHERE proname LIKE 'procrastinate\_%' AND pronamespace = 'public'::regnamespace
            LOOP
                EXECUTE 'DROP ROUTINE ' || f.signature || ' CASCADE';
            END LOOP;
        END $$
        """
    )
    op.execute("DROP TYPE procrastinate_job_to_defer_v1, procrastinate_job_event_type, procrastinate_job_status")
