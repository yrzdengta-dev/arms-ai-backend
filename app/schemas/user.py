from datetime import datetime

from pydantic import BaseModel


class UserRead(BaseModel):
    id: str
    arms_account: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
