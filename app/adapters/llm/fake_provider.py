import hashlib
import json
import logging
from typing import Any, Protocol

from pydantic import BaseModel

from app.schemas.audit import AuditOutput, Decision, Evidence, RuleResult

logger = logging.getLogger(__name__)


class AuditModelRequest(BaseModel):
    prompt: str
    order_snapshot: dict[str, Any]
    pdf_text: str = ""
    skill_id: str = ""
    skill_version: str = ""


class AuditModelResponse(BaseModel):
    decision: str
    raw_output: dict[str, Any]
    normalized_output: AuditOutput
    model_provider: str
    model_name: str
    input_hash: str


class AuditModelProvider(Protocol):
    async def audit(self, request: AuditModelRequest) -> AuditModelResponse:
        ...


class FakeAuditProvider:
    async def audit(self, request: AuditModelRequest) -> AuditModelResponse:
        input_payload = json.dumps(
            {"prompt": request.prompt, "pdf_text_len": len(request.pdf_text)},
            sort_keys=True,
        )
        input_hash = hashlib.sha256(input_payload.encode()).hexdigest()

        has_pdf = len(request.pdf_text) > 20
        skc = request.order_snapshot.get("skc", "")

        if not has_pdf:
            output = AuditOutput(
                decision=Decision.MANUAL_REVIEW,
                summary="Insufficient PDF text for automated review",
                rules=[],
                manual_review_reasons=["PDF text is too short or missing"],
            )
        elif not skc:
            output = AuditOutput(
                decision=Decision.MANUAL_REVIEW,
                summary="Missing SKC in order snapshot",
                rules=[],
                manual_review_reasons=["Required field SKC is missing"],
            )
        else:
            output = AuditOutput(
                decision=Decision.PASS,
                summary=f"Certificate for SKC {skc} passes automated review",
                rules=[
                    RuleResult(
                        rule_id="FAKE-001",
                        result=Decision.PASS,
                        reason="Fake provider: all checks passed",
                        evidence=[
                            Evidence(
                                file_name="report.pdf",
                                page=1,
                                quote="[Fake audit evidence]",
                            )
                        ],
                    )
                ],
                manual_review_reasons=[],
            )

        logger.info("FakeProvider audit decision=%s skill=%s", output.decision.value, request.skill_id)

        return AuditModelResponse(
            decision=output.decision.value,
            raw_output=output.model_dump(),
            normalized_output=output,
            model_provider="fake",
            model_name="fake-v1",
            input_hash=input_hash,
        )
