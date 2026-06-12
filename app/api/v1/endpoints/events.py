import logging

from fastapi import APIRouter, Depends, Header
from fastapi.responses import StreamingResponse

from app.core.config import get_settings
from app.core.database import _get_session_factory
from app.core.identity import can_view_all_orders, get_current_user
from app.models.user import User
from app.repositories.order_repository import Scope
from app.services.event_service import event_stream

logger = logging.getLogger(__name__)
router = APIRouter()


def _get_event_scope(user: User) -> Scope:
    settings = get_settings()
    if can_view_all_orders(user, settings.admin_account_set):
        return "all"
    return "own"


@router.get("/stream")
async def stream_events(
    last_event_id: int = Header(default=0, alias="Last-Event-ID"),
    current_user: User = Depends(get_current_user),
):
    scope = _get_event_scope(current_user)

    def _db_factory():
        factory = _get_session_factory()
        return factory()

    return StreamingResponse(
        event_stream(
            db_factory=_db_factory,
            owner_user_id=current_user.id,
            last_event_id=last_event_id,
            scope=scope,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
