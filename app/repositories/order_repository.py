import hashlib
import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

from sqlalchemy import and_, exists, func, or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.selectable import Subquery

from app.models.audit_result import AuditResult
from app.models.order import Order

Scope = Literal["own", "all"]
_VOLATILE_HASH_KEYS = {"collected_at", "collection_run_id"}
_URL_HASH_KEYS = {"url", "source_url", "internal_url"}

_SIGNATURE_QUERY_PARAMS: set[str] = {
    # AWS S3 / CloudFront pre-signed URL
    "x-amz-algorithm", "x-amz-credential", "x-amz-date",
    "x-amz-expires", "x-amz-signedheaders", "x-amz-signature",
    "x-amz-security-token", "awsaccesskeyid",
    # Google Cloud Storage signed URL
    "expires", "signature", "googleaccessid",
    # Azure Blob SAS token (signature params only)
    "sig", "se", "sp", "spr", "st", "sv", "sip", "ske", "skoid", "sktid", "skv",
    # Generic / CDN / auth tokens
    "token",
    "response-content-disposition", "response-content-type",
    "response-cache-control", "response-expires",
}


def compute_detail_hash(
    order_snapshot: dict[str, Any],
    raw_detail: dict[str, Any],
    pdf_files: Sequence[Any] | None = None,
) -> str:
    payload = json.dumps(
        {
            "snapshot": _canonicalize_for_hash(order_snapshot),
            "detail": _canonicalize_for_hash(raw_detail),
            "pdf_files": _canonicalize_for_hash([
                item.model_dump() if hasattr(item, "model_dump") else item
                for item in (pdf_files or [])
            ]),
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _canonicalize_for_hash(value: Any, key: str = "") -> Any:
    if isinstance(value, dict):
        return {
            item_key: _canonicalize_for_hash(item_value, item_key)
            for item_key, item_value in value.items()
            if item_key not in _VOLATILE_HASH_KEYS
        }
    if isinstance(value, list):
        return [_canonicalize_for_hash(item, key) for item in value]
    if isinstance(value, str) and (key in _URL_HASH_KEYS or key.endswith("_url")):
        parsed = urlsplit(value)
        if parsed.scheme in {"http", "https"}:
            filtered_query = _filter_signature_query(parsed.query)
            return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, filtered_query, ""))
    return value


def _filter_signature_query(query: str) -> str:
    """Remove signature/time-limited params, keep the rest (sorted)."""
    if not query:
        return ""
    from urllib.parse import parse_qsl, urlencode
    params = parse_qsl(query, keep_blank_values=False)
    kept = [(k, v) for k, v in params if k.lower() not in _SIGNATURE_QUERY_PARAMS]
    kept.sort(key=lambda x: x[0])
    return urlencode(kept) if kept else ""


def _current_result_subq() -> Subquery:
    """Return audit results keyed by both order id and order version."""
    return select(
        AuditResult.order_id,
        AuditResult.order_version,
        AuditResult.decision,
    ).subquery()


def _latest_result_subq() -> Subquery:
    """Backward-compatible alias for callers using the former helper name."""
    return _current_result_subq()


def _current_decision_exists(decision: str):
    return exists(
        select(AuditResult.id).where(
            AuditResult.order_id == Order.id,
            AuditResult.order_version == Order.order_version,
            AuditResult.decision == decision,
        )
    )


class OrderRepository:
    model = Order

    async def get_by_id(self, db: AsyncSession, order_id: str) -> Order | None:
        result = await db.execute(select(Order).where(Order.id == order_id))
        return result.scalars().first()

    async def get_by_task_order_id(
        self, db: AsyncSession, task_order_id: str
    ) -> Order | None:
        result = await db.execute(
            select(Order).where(Order.task_order_id == task_order_id)
        )
        return result.scalars().first()

    async def get_by_task_order_id_and_owner(
        self, db: AsyncSession, task_order_id: str, owner_user_id: str
    ) -> Order | None:
        result = await db.execute(
            select(Order).where(
                and_(Order.task_order_id == task_order_id, Order.owner_user_id == owner_user_id)
            )
        )
        return result.scalars().first()

    async def list_orders(
        self,
        db: AsyncSession,
        owner_user_id: str,
        skip: int = 0,
        limit: int = 50,
        pipeline_status: str | None = None,
        decision: str | None = None,
        business_type: str | None = None,
        scene_id: str | None = None,
        audit_point_id: str | None = None,
        search: str | None = None,
        scope: Scope = "own",
        cert_type: str | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        arms_audit_status: str | None = None,
        arms_audit_result: str | None = None,
    ) -> Sequence[Order]:
        conditions = []
        if scope == "own":
            conditions.append(Order.owner_user_id == owner_user_id)

        if pipeline_status:
            conditions.append(Order.pipeline_status == pipeline_status)
        if business_type:
            conditions.append(Order.business_type == business_type)
        if scene_id:
            conditions.append(Order.scene_id == scene_id)
        if audit_point_id:
            conditions.append(Order.audit_point_id == audit_point_id)
        if search:
            conditions.append(
                or_(
                    Order.task_order_id.ilike(f"%{search}%"),
                    Order.order_snapshot["skc"].as_string().ilike(f"%{search}%"),
                    Order.order_snapshot["product_name"].as_string().ilike(f"%{search}%"),
                    Order.order_snapshot["supplier_name"].as_string().ilike(f"%{search}%"),
                )
            )
        if cert_type:
            cert_values = [v.strip() for v in cert_type.split(",") if v.strip()]
            if cert_values:
                conditions.append(
                    Order.order_snapshot["certificate_type_name"].as_string().in_(cert_values)
                )
        if created_after is not None:
            conditions.append(Order.created_at >= created_after)
        if created_before is not None:
            conditions.append(Order.created_at <= created_before)
        if arms_audit_status:
            conditions.append(Order.arms_audit_status == arms_audit_status)
        if arms_audit_result:
            conditions.append(Order.arms_audit_result == arms_audit_result)
        if decision:
            conditions.append(_current_decision_exists(decision))

        stmt = (
            select(Order)
            .where(and_(*conditions) if conditions else true())
            .order_by(Order.created_at.desc())
            .offset(skip)
            .limit(limit)
        )
        result = await db.execute(stmt)
        return result.scalars().all()

    async def count_orders(
        self,
        db: AsyncSession,
        owner_user_id: str,
        pipeline_status: str | None = None,
        decision: str | None = None,
        business_type: str | None = None,
        scene_id: str | None = None,
        audit_point_id: str | None = None,
        search: str | None = None,
        scope: Scope = "own",
        cert_type: str | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        arms_audit_status: str | None = None,
        arms_audit_result: str | None = None,
    ) -> int:
        conditions = []
        if scope == "own":
            conditions.append(Order.owner_user_id == owner_user_id)

        if pipeline_status:
            conditions.append(Order.pipeline_status == pipeline_status)
        if business_type:
            conditions.append(Order.business_type == business_type)
        if scene_id:
            conditions.append(Order.scene_id == scene_id)
        if audit_point_id:
            conditions.append(Order.audit_point_id == audit_point_id)
        if search:
            conditions.append(
                or_(
                    Order.task_order_id.ilike(f"%{search}%"),
                    Order.order_snapshot["skc"].as_string().ilike(f"%{search}%"),
                    Order.order_snapshot["product_name"].as_string().ilike(f"%{search}%"),
                    Order.order_snapshot["supplier_name"].as_string().ilike(f"%{search}%"),
                )
            )
        if cert_type:
            cert_values = [v.strip() for v in cert_type.split(",") if v.strip()]
            if cert_values:
                conditions.append(
                    Order.order_snapshot["certificate_type_name"].as_string().in_(cert_values)
                )
        if created_after is not None:
            conditions.append(Order.created_at >= created_after)
        if created_before is not None:
            conditions.append(Order.created_at <= created_before)
        if arms_audit_status:
            conditions.append(Order.arms_audit_status == arms_audit_status)
        if arms_audit_result:
            conditions.append(Order.arms_audit_result == arms_audit_result)
        if decision:
            conditions.append(_current_decision_exists(decision))

        stmt = select(func.count()).select_from(Order).where(and_(*conditions) if conditions else true())
        result = await db.execute(stmt)
        return result.scalar() or 0

    async def get_stats(
        self, db: AsyncSession, owner_user_id: str, scope: Scope = "own",
    ) -> dict[str, Any]:
        conditions = []
        if scope == "own":
            conditions.append(Order.owner_user_id == owner_user_id)

        total_stmt = select(func.count()).select_from(Order)
        total_stmt = total_stmt.where(and_(*conditions) if conditions else true())
        total_result = await db.execute(total_stmt)
        total = total_result.scalar() or 0

        pipeline_stmt = select(Order.pipeline_status, func.count())
        pipeline_stmt = pipeline_stmt.where(and_(*conditions) if conditions else true())
        pipeline_stmt = pipeline_stmt.group_by(Order.pipeline_status)
        pipeline_result = await db.execute(pipeline_stmt)
        by_pipeline = {row[0]: row[1] for row in pipeline_result}

        current = _current_result_subq()

        decision_stmt = (
            select(current.c.decision, func.count())
            .select_from(Order)
            .join(
                current,
                and_(
                    Order.id == current.c.order_id,
                    Order.order_version == current.c.order_version,
                ),
                isouter=True,
            )
        )
        decision_stmt = decision_stmt.where(and_(*conditions) if conditions else true())
        decision_stmt = decision_stmt.group_by(current.c.decision)
        decision_result = await db.execute(decision_stmt)
        by_decision = {row[0] or "PENDING": row[1] for row in decision_result}

        return {"total": total, "by_pipeline_status": by_pipeline, "by_decision": by_decision}


order_repository = OrderRepository()
