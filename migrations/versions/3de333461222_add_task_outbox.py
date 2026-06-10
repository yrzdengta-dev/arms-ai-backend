"""add_task_outbox

Revision ID: 3de333461222
Revises: 001
Create Date: 2026-06-10 14:51:38.113705
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '3de333461222'
down_revision: Union[str, None] = '001'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('task_outbox',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('order_id', sa.String(length=36), nullable=False),
    sa.Column('order_version', sa.Integer(), nullable=False),
    sa.Column('task_type', sa.String(length=64), nullable=False),
    sa.Column('task_payload', sa.JSON(), nullable=False),
    sa.Column('dispatched', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_task_outbox_order_id'), 'task_outbox', ['order_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_task_outbox_order_id'), table_name='task_outbox')
    op.drop_table('task_outbox')
