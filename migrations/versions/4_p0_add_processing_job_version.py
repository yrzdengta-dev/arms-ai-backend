"""P0 fix: add order_version to processing_jobs, ensure order_files columns

Revision ID: 4_p0_add_processing_job_version
Revises: 3de333461222
Create Date: 2026-06-11
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "4_p0_add_processing_job_version"
down_revision: Union[str, None] = "3de333461222"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ---- order_files: add columns that may be missing from older DBs ----
    # The 001_initial.py migration may have been applied BEFORE these columns
    # were added. Use raw SQL to add them idempotently.
    for col_name, col_type, col_default in [
        ("order_version", "INTEGER", "1"),
        ("internal_url", "VARCHAR(2048)", None),
    ]:
        _add_column_if_not_exists("order_files", col_name, col_type, col_default)

    # Backfill order_files.order_version for any NULL rows (shouldn't exist, but be safe)
    op.execute(
        "UPDATE order_files SET order_version = 1 WHERE order_version IS NULL"
    )

    # ---- processing_jobs: add order_version column ----
    _add_column_if_not_exists("processing_jobs", "order_version", "INTEGER", "1")

    # Backfill existing rows
    op.execute(
        "UPDATE processing_jobs SET order_version = 1 WHERE order_version IS NULL"
    )

    # Ensure NOT NULL after backfill
    op.execute(
        "ALTER TABLE processing_jobs ALTER COLUMN order_version SET NOT NULL"
    )

    # Add composite index for job lookup by (order_id, order_version, job_type)
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_processing_jobs_order_version_type "
        "ON processing_jobs (order_id, order_version, job_type)"
    )


def downgrade() -> None:
    # Remove the composite index
    op.execute(
        "DROP INDEX IF EXISTS ix_processing_jobs_order_version_type"
    )

    # Drop order_version from processing_jobs
    op.execute(
        "ALTER TABLE processing_jobs DROP COLUMN IF EXISTS order_version"
    )

    # Do NOT drop order_files columns — they may have been created by
    # the initial migration and dropping them would cause data loss.
    # The downgrade is intentionally partial for order_files.


def _add_column_if_not_exists(table: str, col_name: str, col_type: str, default: str | None) -> None:
    """Add a column if it doesn't already exist."""
    # Check if column exists
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = :table AND column_name = :col"
        ),
        {"table": table, "col": col_name},
    )
    if result.fetchone() is None:
        default_clause = f" DEFAULT {default}" if default is not None else ""
        op.execute(
            f"ALTER TABLE {table} ADD COLUMN {col_name} {col_type}{default_clause}"
        )
