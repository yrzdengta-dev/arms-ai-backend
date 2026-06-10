from fastapi import APIRouter

from app.api.v1.endpoints import events, orders

api_router = APIRouter()
api_router.include_router(orders.router, prefix="/orders", tags=["orders"])
api_router.include_router(events.router, prefix="/events", tags=["events"])
