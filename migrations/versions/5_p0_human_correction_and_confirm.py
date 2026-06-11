"""P0: add human_decision, correction_history, confirmed_by, confirmed_at to orders

Revision ID: 5_p0_human_correction
Revises: 4_p0_add_processing_job_version
Create Date: 2026-06-11
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "5_p0_human_correction"
down_revision: Union[str, None] = "4_p0_add_processing_job_version"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    for col_name, col_type, col_default in [
        ("human_decision", "VARCHAR(32)", None),
        ("correction_history", "JSON", None),
        ("confirmed_by", "VARCHAR(36)", None),
        ("confirmed_at", "TIMESTAMP WITHOUT TIME ZONE", None),
    ]:
        _add_column_if_not_exists("orders", col_name, col_type, col_default)


def downgrade() -> None:
    for col_name in ["confirmed_at", "confirmed_by", "correction_history", "human_decision"]:
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
