"""P2: add arms_audit_status, arms_audit_result, arms_reject_reason, arms_status_synced_at to orders

Revision ID: 6_p2_arms_audit_result
Revises: 5_p0_human_correction
Create Date: 2026-06-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "6_p2_arms_audit_result"
down_revision: Union[str, None] = "5_p0_human_correction"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Columns with their types and optional defaults
    for col_name, col_type, col_default in [
        ("arms_audit_status", "VARCHAR(16)", None),
        ("arms_audit_result", "VARCHAR(32)", None),
        ("arms_reject_reason", "VARCHAR(512)", None),
        ("arms_status_synced_at", "TIMESTAMP WITHOUT TIME ZONE", None),
    ]:
        _add_column_if_not_exists("orders", col_name, col_type, col_default)

    # Create indexes on the two filter columns
    for col_name in ["arms_audit_status", "arms_audit_result"]:
        _create_index_if_not_exists("orders", col_name)


def downgrade() -> None:
    for col_name in [
        "arms_status_synced_at", "arms_reject_reason", "arms_audit_result", "arms_audit_status",
    ]:
        op.execute(f"ALTER TABLE orders DROP COLUMN IF EXISTS {col_name}")


def _add_column_if_not_exists(table: str, col_name: str, col_type: str, default: str | None) -> None:
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


def _create_index_if_not_exists(table: str, col_name: str) -> None:
    conn = op.get_bind()
    idx_name = f"ix_orders_{col_name}"
    dialect_name = conn.dialect.name if hasattr(conn, 'dialect') else ''
    if dialect_name == 'sqlite':
        # SQLite: check sqlite_master
        row = conn.execute(
            sa.text(
                "SELECT name FROM sqlite_master "
                "WHERE type='index' AND tbl_name=:table AND name=:idx"
            ),
            {"table": table, "idx": idx_name},
        ).fetchone()
    else:
        # PostgreSQL: check pg_indexes
        row = conn.execute(
            sa.text(
                "SELECT indexname FROM pg_indexes "
                "WHERE tablename = :table AND indexname = :idx"
            ),
            {"table": table, "idx": idx_name},
        ).fetchone()
    if row is None:
        op.create_index(idx_name, table, [col_name])
