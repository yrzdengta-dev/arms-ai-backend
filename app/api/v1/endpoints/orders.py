import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.identity import get_current_user
from app.models.user import User
from app.repositories.order_repository import order_repository
from app.schemas.order import (
    BatchIngestRequest,
    OrderDetailResponse,
    OrderIngestRequest,
    OrderIngestResponse,
    OrderListItem,
    OrderListResponse,
    OrderResultResponse,
    OrderStatsResponse,
)
from app.services.order_service import CrossUserConflictError, order_service

logger = logging.getLogger(__name__)
router = APIRouter()


@router.post("/ingest", response_model=OrderIngestResponse, status_code=status.HTTP_200_OK)
async def ingest_order(
    request: OrderIngestRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        order, created = await order_service.ingest(db, request, current_user)
    except CrossUserConflictError as e:
        raise HTTPException(
            status_code=409,
            detail=f"task_order_id={e.task_order_id} already belongs to another user",
        ) from e
    return OrderIngestResponse(
        order_id=order.id,
        task_order_id=order.task_order_id,
        order_version=order.order_version,
        pipeline_status=order.pipeline_status,
        created=created,
    )


@router.post("/batch-ingest", status_code=status.HTTP_200_OK)
async def batch_ingest(
    request: BatchIngestRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    results = []
    for item in request.orders:
        order, created = await order_service.ingest(db, item, current_user)
        results.append(
            OrderIngestResponse(
                order_id=order.id,
                task_order_id=order.task_order_id,
                order_version=order.order_version,
                pipeline_status=order.pipeline_status,
                created=created,
            )
        )
    return {"results": [r.model_dump() for r in results], "count": len(results)}


@router.get("", response_model=OrderListResponse)
async def list_orders(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    pipeline_status: str | None = Query(None),
    decision: str | None = Query(None),
    business_type: str | None = Query(None),
    scene_id: str | None = Query(None),
    audit_point_id: str | None = Query(None),
    search: str | None = Query(None, max_length=128),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    skip = (page - 1) * page_size
    orders = await order_repository.list_orders(
        db,
        owner_user_id=current_user.id,
        skip=skip,
        limit=page_size,
        pipeline_status=pipeline_status,
        decision=decision,
        business_type=business_type,
        scene_id=scene_id,
        audit_point_id=audit_point_id,
        search=search,
    )
    total = await order_repository.count_orders(
        db,
        owner_user_id=current_user.id,
        pipeline_status=pipeline_status,
        decision=decision,
        business_type=business_type,
        scene_id=scene_id,
        audit_point_id=audit_point_id,
        search=search,
    )

    items = [
        OrderListItem(
            order_id=o.id,
            task_order_id=o.task_order_id,
            scene_id=o.scene_id,
            audit_point_id=o.audit_point_id,
            business_type=o.business_type,
            pipeline_status=o.pipeline_status,
            business_status=o.business_status,
            order_version=o.order_version,
            decision=None,
            skc=(o.order_snapshot or {}).get("skc", ""),
            product_name=(o.order_snapshot or {}).get("product_name", ""),
            created_at=o.created_at,
            updated_at=o.updated_at,
        )
        for o in orders
    ]
    return OrderListResponse(items=items, total=total)


@router.get("/stats", response_model=OrderStatsResponse)
async def get_stats(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    stats = await order_repository.get_stats(db, owner_user_id=current_user.id)
    return OrderStatsResponse(**stats)


@router.get("/{task_order_id}", response_model=OrderDetailResponse)
async def get_order_detail(
    task_order_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    order = await order_service.get_order_for_user(db, task_order_id, current_user.id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")
    return OrderDetailResponse(
        order_id=order.id,
        task_order_id=order.task_order_id,
        task_uuid=order.task_uuid,
        owner_user_id=order.owner_user_id,
        scene_id=order.scene_id,
        audit_point_id=order.audit_point_id,
        audit_node=order.audit_node,
        business_type=order.business_type,
        business_status=order.business_status,
        pipeline_status=order.pipeline_status,
        order_version=order.order_version,
        detail_hash=order.detail_hash,
        order_snapshot=order.order_snapshot,
        raw_detail=order.raw_detail,
        created_at=order.created_at,
        updated_at=order.updated_at,
    )


@router.post("/{task_order_id}/retry")
async def retry_order(
    task_order_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    order = await order_service.retry_order(db, task_order_id, current_user.id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found or retry not allowed")
    return {
        "order_id": order.id,
        "task_order_id": order.task_order_id,
        "order_version": order.order_version,
        "pipeline_status": order.pipeline_status,
    }


@router.get("/{task_order_id}/result", response_model=OrderResultResponse)
async def get_order_result(
    task_order_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    order = await order_service.get_order_for_user(db, task_order_id, current_user.id)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")

    from sqlalchemy import select

    from app.models.audit_result import AuditResult

    result_stmt = (
        select(AuditResult)
        .where(AuditResult.order_id == order.id)
        .order_by(AuditResult.order_version.desc())
        .limit(1)
    )
    result_row = (await db.execute(result_stmt)).scalars().first()

    if result_row is None:
        return OrderResultResponse(
            order_id=order.id,
            task_order_id=order.task_order_id,
            pipeline_status=order.pipeline_status,
            decision=None,
            summary=None,
            rules=[],
            model_provider=None,
            model_name=None,
            skill_id=None,
            skill_version=None,
            prompt_hash=None,
            order_version=order.order_version,
            updated_at=order.updated_at,
        )

    from app.schemas.order import EvidenceItem, RuleResultItem

    rules: list[RuleResultItem] = []
    normalized = result_row.normalized_output or {}
    for r in normalized.get("rules", []):
        evidence = []
        for ev in r.get("evidence", []):
            evidence.append(EvidenceItem(
                file_name=ev.get("file_name", ""),
                page=ev.get("page", 1),
                quote=ev.get("quote", ""),
            ))
        rules.append(RuleResultItem(
            rule_id=r.get("rule_id", ""),
            result=r.get("result", ""),
            reason=r.get("reason", ""),
            evidence=evidence,
        ))

    return OrderResultResponse(
        order_id=order.id,
        task_order_id=order.task_order_id,
        pipeline_status=order.pipeline_status,
        decision=result_row.decision,
        summary=normalized.get("summary", ""),
        rules=rules,
        model_provider=result_row.model_provider,
        model_name=result_row.model_name,
        skill_id=result_row.skill_id,
        skill_version=result_row.skill_version,
        prompt_hash=result_row.prompt_version,
        order_version=result_row.order_version,
        updated_at=order.updated_at,
    )
