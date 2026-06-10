from datetime import datetime
from typing import Any

from pydantic import BaseModel


class OrderEventOut(BaseModel):
    id: int
    order_id: str
    owner_user_id: str
    event_type: str
    order_version: int
    payload: dict[str, Any] | None = None
    created_at: datetime

    model_config = {"from_attributes": True}
