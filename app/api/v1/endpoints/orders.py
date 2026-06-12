import logging
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.identity import can_view_all_orders, get_current_user
from app.models.user import User
from app.repositories.order_repository import Scope, order_repository
from app.schemas.order import (
    BatchConfirmRequest,
    BatchConfirmResponse,
    BatchIngestRequest,
    CorrectionRequest,
    CorrectionResponse,
    OrderDetailResponse,
    OrderIngestRequest,
    OrderIngestResponse,
    OrderListItem,
    OrderListResponse,
    OrderResultResponse,
    OrderStatsResponse,
)
from app.services.correction_service import correction_service
from app.services.order_service import CrossUserConflictError, order_service

logger = logging.getLogger(__name__)
router = APIRouter()


def _get_scope(user: User) -> Scope:
    """Compute read scope from user and settings."""
    settings = get_settings()
    if can_view_all_orders(user, settings.admin_account_set):
        logger.info("Admin read scope: user=%s viewing all orders", user.arms_account)
        return "all"
    return "own"


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
        try:
            order, created = await order_service.ingest(db, item, current_user)
        except CrossUserConflictError as e:
            raise HTTPException(
                status_code=409,
                detail=f"task_order_id={e.task_order_id} already belongs to another user",
            ) from e
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
    cert_type: str | None = Query(None),
    created_after: datetime | None = Query(None),
    created_before: datetime | None = Query(None),
    arms_audit_status: str | None = Query(None),
    arms_audit_result: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    scope = _get_scope(current_user)
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
        cert_type=cert_type,
        created_after=created_after,
        created_before=created_before,
        arms_audit_status=arms_audit_status,
        arms_audit_result=arms_audit_result,
        scope=scope,
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
        cert_type=cert_type,
        created_after=created_after,
        created_before=created_before,
        arms_audit_status=arms_audit_status,
        arms_audit_result=arms_audit_result,
        scope=scope,
    )

    # Query latest decision for each order
    from sqlalchemy import select

    from app.models.audit_result import AuditResult

    decision_stmt = select(
        AuditResult.order_id,
        AuditResult.order_version,
        AuditResult.decision,
    ).where(AuditResult.order_id.in_([o.id for o in orders]))
    decision_rows = (await db.execute(decision_stmt)).all()
    decision_map = {(row[0], row[1]): row[2] for row in decision_rows}

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
            decision=decision_map.get((o.id, o.order_version)),
            human_decision=o.human_decision,
            confirmed_at=o.confirmed_at,
            skc=(o.order_snapshot or {}).get("skc", ""),
            product_name=(o.order_snapshot or {}).get("product_name", ""),
            supplier_name=(o.order_snapshot or {}).get("supplier_name", ""),
            certificate_type_name=(o.order_snapshot or {}).get("certificate_type_name", ""),
            arms_audit_status=o.arms_audit_status,
            arms_audit_result=o.arms_audit_result,
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
    scope = _get_scope(current_user)
    stats = await order_repository.get_stats(db, owner_user_id=current_user.id, scope=scope)
    return OrderStatsResponse(**stats)


@router.get("/{task_order_id}", response_model=OrderDetailResponse)
async def get_order_detail(
    task_order_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    scope = _get_scope(current_user)
    order = await order_service.get_order_for_user(db, task_order_id, current_user.id, scope=scope)
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
        human_decision=order.human_decision,
        correction_history=order.correction_history,
        confirmed_by=order.confirmed_by,
        confirmed_at=order.confirmed_at,
        arms_audit_status=order.arms_audit_status,
        arms_audit_result=order.arms_audit_result,
        arms_reject_reason=order.arms_reject_reason,
        arms_status_synced_at=order.arms_status_synced_at,
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


@router.post("/{task_order_id}/correction", response_model=CorrectionResponse)
async def correct_order(
    task_order_id: str,
    request: CorrectionRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Apply a human correction to an AI audit decision."""
    scope = _get_scope(current_user)
    if request.reason is None or len(request.reason.strip()) == 0:
        raise HTTPException(status_code=400, detail="reason is required")
    # Default operator to current user's arms account
    if not request.operator:
        request.operator = current_user.arms_account
    result = await correction_service.correct(
        db, task_order_id, request, current_user.id, scope=scope,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Order not found")
    return result


@router.post("/batch-confirm", response_model=BatchConfirmResponse)
async def batch_confirm_orders(
    request: BatchConfirmRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Batch confirm orders (idempotent)."""
    scope = _get_scope(current_user)
    return await correction_service.batch_confirm(
        db, request, current_user.id, scope=scope,
    )


@router.get("/{task_order_id}/result", response_model=OrderResultResponse)
async def get_order_result(
    task_order_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    scope = _get_scope(current_user)
    order = await order_service.get_order_for_user(db, task_order_id, current_user.id, scope=scope)
    if order is None:
        raise HTTPException(status_code=404, detail="Order not found")

    from sqlalchemy import select

    from app.models.audit_result import AuditResult

    result_stmt = (
        select(AuditResult)
        .where(
            AuditResult.order_id == order.id,
            AuditResult.order_version == order.order_version,
        )
        .limit(1)
    )
    result_row = (await db.execute(result_stmt)).scalars().first()

    if result_row is None:
        return OrderResultResponse(
            order_id=order.id,
            task_order_id=order.task_order_id,
            pipeline_status=order.pipeline_status,
            decision=None,
            human_decision=order.human_decision,
            correction_history=order.correction_history,
            confirmed_at=order.confirmed_at,
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
        human_decision=order.human_decision,
        correction_history=order.correction_history,
        confirmed_at=order.confirmed_at,
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
@router.post("/clear")
async def clear_orders(
    scope: str = Query("own", regex="^(own|all)$"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Clear all orders and related child-table data (DEV only).

    - scope=own (default): delete only the current user's orders
    - scope=all: delete all orders (requires admin or DEBUG)
    - Production protection: returns 403 unless DEBUG=true or admin
    """
    settings = get_settings()
    is_admin = can_view_all_orders(current_user, settings.admin_account_set)

    if not settings.DEBUG and not is_admin:
        raise HTTPException(
            status_code=403,
            detail="Clear endpoint is only available in DEBUG mode or for admin users",
        )

    if scope == "all" and not is_admin:
        raise HTTPException(
            status_code=403,
            detail="scope=all requires admin privileges",
        )

    repo_scope: Scope = "all" if scope == "all" else "own"
    counts = await order_repository.bulk_delete_by_owner(
        db, current_user.id, scope=repo_scope,
    )
    await db.commit()
    return counts
