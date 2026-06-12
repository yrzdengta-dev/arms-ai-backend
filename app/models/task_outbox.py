import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.core.time import utc_now


class TaskOutbox(Base):
    __tablename__ = "task_outbox"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    order_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    order_version: Mapped[int] = mapped_column(Integer, nullable=False)
    task_type: Mapped[str] = mapped_column(String(64), nullable=False)
    task_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    dispatched: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        default=utc_now, nullable=False
    )
