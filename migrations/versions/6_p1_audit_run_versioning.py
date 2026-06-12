"""P1-3: audit run versioning — remove old unique constraint, add new fields and constraint

Revision ID: 6_p1_audit_run_versioning
Revises: 5_p0_human_correction
Create Date: 2026-06-12
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "6_p1_audit_run_versioning"
down_revision: Union[str, None] = "5_p0_human_correction"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()

    # 1. Drop old unique constraint (order_id, order_version) if it exists.
    #    PostgreSQL auto-names it; look it up dynamically.
    if _is_postgresql(conn):
        result = conn.execute(sa.text(
            "SELECT conname FROM pg_constraint "
            "WHERE conrelid = 'audit_results'::regclass "
            "AND contype = 'u'"
        ))
        for row in result:
            conname = row[0]
            if "order_id" in conname and "order_version" in conname and "input_hash" not in conname:
                op.drop_constraint(conname, "audit_results", type_="unique")
    else:
        # SQLite: just drop and recreate the unique constraint
        pass

    # 2. Add new columns if they don't exist
    for column in [
        sa.Column(
            "protocol_version",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'COMPLETED'"),
        ),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("rules_hash", sa.String(length=128), nullable=True),
    ]:
        _add_column_if_not_exists("audit_results", column)

    # 3. Add new unique constraint (order_id, order_version, input_hash)
    if _is_postgresql(conn):
        # Check if new constraint already exists
        result = conn.execute(sa.text(
            "SELECT 1 FROM pg_constraint "
            "WHERE conrelid = 'audit_results'::regclass "
            "AND conname = 'uq_audit_results_order_version_input'"
        ))
        if not result.fetchone():
            op.create_unique_constraint(
                "uq_audit_results_order_version_input",
                "audit_results",
                ["order_id", "order_version", "input_hash"],
            )


def downgrade() -> None:
    conn = op.get_bind()

    # 1. Drop new unique constraint
    if _is_postgresql(conn):
        op.drop_constraint(
            "uq_audit_results_order_version_input",
            "audit_results",
            type_="unique",
        )

    # 2. Remove new columns
    for col_name in ["rules_hash", "completed_at", "status", "protocol_version"]:
        op.execute(f"ALTER TABLE audit_results DROP COLUMN IF EXISTS {col_name}")

    # 3. Restore old unique constraint (only if no duplicate (order_id, order_version) exist)
    if _is_postgresql(conn):
        # Check for duplicates that would prevent restoration
        dup_check = conn.execute(sa.text(
            "SELECT order_id, order_version, COUNT(*) as cnt "
            "FROM audit_results "
            "GROUP BY order_id, order_version "
            "HAVING COUNT(*) > 1"
        ))
        duplicates = dup_check.fetchall()
        if duplicates:
            raise RuntimeError(
                f"Cannot restore old unique constraint: {len(duplicates)} duplicate "
                f"(order_id, order_version) pairs exist. Downgrade is not safe. "
                f"First duplicates: {duplicates[:3]}"
            )
        op.create_unique_constraint(
            "uq_audit_results_order_version",
            "audit_results",
            ["order_id", "order_version"],
        )


def _add_column_if_not_exists(table: str, column: sa.Column) -> None:
    conn = op.get_bind()
    result = conn.execute(
        sa.text(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = :table AND column_name = :col"
        ),
        {"table": table, "col": column.name},
    )
    if result.fetchone() is None:
        op.add_column(table, column)


def _is_postgresql(conn) -> bool:
    try:
        return "postgresql" in str(conn.engine.url)
    except Exception:
        return False
