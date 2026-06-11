from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class PdfFileItem(BaseModel):
    name: str = ""
    url: str = ""
    internal_url: str = ""


class OrderIngestRequest(BaseModel):
    task_order_id: str = Field(..., min_length=1, max_length=128)
    task_uuid: str = ""
    scene_id: str = ""
    audit_point_id: str = ""
    audit_node: str = ""
    business_type: str | None = None
    order_snapshot: dict[str, Any] = {}
    raw_detail: dict[str, Any] = {}
    pdf_files: list[PdfFileItem] = []


class BatchIngestRequest(BaseModel):
    orders: list[OrderIngestRequest] = Field(..., min_length=1, max_length=100)


class OrderIngestResponse(BaseModel):
    order_id: str
    task_order_id: str
    order_version: int
    pipeline_status: str
    created: bool


class OrderListItem(BaseModel):
    order_id: str
    task_order_id: str
    scene_id: str | None = None
    audit_point_id: str | None = None
    business_type: str | None = None
    pipeline_status: str
    business_status: str | None = None
    order_version: int
    decision: str | None = None
    human_decision: str | None = None
    confirmed_at: datetime | None = None
    skc: str | None = ""
    product_name: str | None = ""
    supplier_name: str | None = ""
    certificate_type_name: str | None = ""
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class OrderListResponse(BaseModel):
    items: list[OrderListItem]
    total: int


class OrderDetailResponse(BaseModel):
    order_id: str
    task_order_id: str
    task_uuid: str | None = None
    owner_user_id: str
    scene_id: str | None = None
    audit_point_id: str | None = None
    audit_node: str | None = None
    business_type: str | None = None
    business_status: str | None = None
    pipeline_status: str
    order_version: int
    detail_hash: str | None = None
    order_snapshot: dict[str, Any] | None = None
    raw_detail: dict[str, Any] | None = None
    human_decision: str | None = None
    correction_history: list[dict[str, Any]] | None = None
    confirmed_by: str | None = None
    confirmed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class OrderStatsResponse(BaseModel):
    total: int
    by_pipeline_status: dict[str, int]
    by_decision: dict[str, int]


class EvidenceItem(BaseModel):
    file_name: str = ""
    page: int = 1
    quote: str = ""


class RuleResultItem(BaseModel):
    rule_id: str
    result: str
    reason: str = ""
    evidence: list[EvidenceItem] = []


class OrderResultResponse(BaseModel):
    order_id: str
    task_order_id: str
    pipeline_status: str
    decision: str | None = None
    human_decision: str | None = None
    correction_history: list[dict[str, Any]] | None = None
    confirmed_at: datetime | None = None
    summary: str | None = None
    rules: list[RuleResultItem] = []
    model_provider: str | None = None
    model_name: str | None = None
    skill_id: str | None = None
    skill_version: str | None = None
    prompt_hash: str | None = None
    order_version: int
    updated_at: datetime


# ---- Human Correction (P0) ----

from typing import Literal

CorrectionDecision = Literal["PASS", "REJECT", "MANUAL_REVIEW"]


class CorrectionRequest(BaseModel):
    decision: CorrectionDecision  # type: ignore[valid-type]
    reason: str = Field(..., min_length=1, max_length=500)
    operator: str | None = None


class CorrectionHistoryEntry(BaseModel):
    operated_at: datetime
    operator: str
    from_decision: str
    to_decision: str
    reason: str


class CorrectionResponse(BaseModel):
    task_order_id: str
    ai_decision: str | None = None
    human_result: str | None = None
    correction_history: list[CorrectionHistoryEntry] = []
    pipeline_status: str


# ---- Batch Confirm (P0) ----

class BatchConfirmRequest(BaseModel):
    task_order_ids: list[str] = Field(..., min_length=1, max_length=200)


class BatchConfirmResultItem(BaseModel):
    task_order_id: str
    status: str  # confirmed | already_confirmed | skipped
    confirmed_at: datetime | None = None
    reason: str | None = None  # only for skipped


class BatchConfirmSummary(BaseModel):
    total: int
    confirmed: int = 0
    already_confirmed: int = 0
    skipped: int = 0


class BatchConfirmResponse(BaseModel):
    results: list[BatchConfirmResultItem]
    summary: BatchConfirmSummary
