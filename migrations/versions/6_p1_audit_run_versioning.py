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
    for col_name, col_type, col_default in [
        ("protocol_version", "INTEGER", "1"),
        ("status", "VARCHAR(32)", "'COMPLETED'"),
        ("completed_at", "TIMESTAMP WITHOUT TIME ZONE", None),
        ("rules_hash", "VARCHAR(128)", None),
    ]:
        _add_column_if_not_exists("audit_results", col_name, col_type, col_default)

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
        if default:
            op.add_column(table, sa.Column(col_name, sa.Text, server_default=sa.text(default)))
        else:
            op.add_column(table, sa.Column(col_name, sa.Text, nullable=True))


def _is_postgresql(conn) -> bool:
    try:
        return "postgresql" in str(conn.engine.url)
    except Exception:
        return False
