from enum import StrEnum

from pydantic import BaseModel


class Decision(StrEnum):
    PASS = "PASS"
    REJECT = "REJECT"
    MANUAL_REVIEW = "MANUAL_REVIEW"


class Evidence(BaseModel):
    file_name: str
    page: int = 1
    quote: str = ""


class RuleResult(BaseModel):
    rule_id: str
    result: Decision
    reason: str = ""
    evidence: list[Evidence] = []


class AuditOutput(BaseModel):
    decision: Decision
    summary: str = ""
    rules: list[RuleResult] = []
    manual_review_reasons: list[str] = []
