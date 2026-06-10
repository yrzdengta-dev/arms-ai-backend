import logging

from fastapi import APIRouter, Depends, Header
from fastapi.responses import StreamingResponse

from app.core.database import _get_session_factory
from app.core.identity import get_current_user
from app.models.user import User
from app.services.event_service import event_stream

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/stream")
async def stream_events(
    last_event_id: int = Header(default=0, alias="Last-Event-ID"),
    current_user: User = Depends(get_current_user),
):
    async def _db_factory():
        factory = _get_session_factory()
        return factory()

    return StreamingResponse(
        event_stream(
            db_factory=_db_factory,
            owner_user_id=current_user.id,
            last_event_id=last_event_id,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
