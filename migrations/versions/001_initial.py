"""Initial schema

Revision ID: 001
Revises:
Create Date: 2026-06-10
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("arms_account", sa.String(128), unique=True, nullable=False, index=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "orders",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("task_order_id", sa.String(128), unique=True, nullable=False, index=True),
        sa.Column("task_uuid", sa.String(128), nullable=True),
        sa.Column("owner_user_id", sa.String(36), nullable=False, index=True),
        sa.Column("scene_id", sa.String(32), nullable=True),
        sa.Column("audit_point_id", sa.String(32), nullable=True),
        sa.Column("audit_node", sa.String(128), nullable=True),
        sa.Column("business_type", sa.String(64), nullable=True),
        sa.Column("business_status", sa.String(32), nullable=True),
        sa.Column("pipeline_status", sa.String(32), nullable=False, server_default="RECEIVED"),
        sa.Column("order_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("detail_hash", sa.String(128), nullable=True),
        sa.Column("order_snapshot", postgresql.JSONB, nullable=True),
        sa.Column("raw_detail", postgresql.JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "order_files",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("order_id", sa.String(36), sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("order_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("original_name", sa.String(512), nullable=False),
        sa.Column("source_url", sa.String(2048), nullable=True),
        sa.Column("internal_url", sa.String(2048), nullable=True),
        sa.Column("storage_key", sa.String(512), nullable=True),
        sa.Column("sha256", sa.String(64), nullable=True, index=True),
        sa.Column("content_type", sa.String(128), nullable=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=True),
        sa.Column("parse_status", sa.String(32), nullable=False, server_default="PENDING"),
        sa.Column("parsed_text", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "processing_jobs",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("order_id", sa.String(36), sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("job_type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="PENDING"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "audit_results",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("order_id", sa.String(36), sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("order_version", sa.Integer(), nullable=False),
        sa.Column("decision", sa.String(32), nullable=True),
        sa.Column("business_type", sa.String(64), nullable=True),
        sa.Column("skill_id", sa.String(128), nullable=True),
        sa.Column("skill_version", sa.String(32), nullable=True),
        sa.Column("prompt_version", sa.String(64), nullable=True),
        sa.Column("model_provider", sa.String(64), nullable=True),
        sa.Column("model_name", sa.String(128), nullable=True),
        sa.Column("input_hash", sa.String(128), nullable=True),
        sa.Column("raw_output", postgresql.JSONB, nullable=True),
        sa.Column("normalized_output", postgresql.JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("order_id", "order_version"),
    )

    op.create_table(
        "order_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("order_id", sa.String(36), sa.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("owner_user_id", sa.String(36), nullable=False, index=True),
        sa.Column("event_type", sa.String(64), nullable=False, index=True),
        sa.Column("order_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("payload", postgresql.JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now(), index=True),
    )



def downgrade() -> None:
    op.drop_table("order_events")
    op.drop_table("audit_results")
    op.drop_table("processing_jobs")
    op.drop_table("order_files")
    op.drop_table("orders")
    op.drop_table("users")
