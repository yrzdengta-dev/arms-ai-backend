import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.time import utc_now


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (UniqueConstraint("task_order_id"),)

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    task_order_id: Mapped[str] = mapped_column(String(128), unique=True, nullable=False, index=True)
    task_uuid: Mapped[str | None] = mapped_column(String(128), nullable=True)
    owner_user_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    scene_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    audit_point_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    audit_node: Mapped[str | None] = mapped_column(String(128), nullable=True)
    business_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    business_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    pipeline_status: Mapped[str] = mapped_column(String(32), nullable=False, default="RECEIVED")
    order_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    detail_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    order_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    raw_detail: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    # Human correction fields (P0: audit adjudication layer, decoupled from pipeline_status)
    human_decision: Mapped[str | None] = mapped_column(String(32), nullable=True)
    correction_history: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True, default=list)
    confirmed_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        default=utc_now, onupdate=utc_now, nullable=False
    )
