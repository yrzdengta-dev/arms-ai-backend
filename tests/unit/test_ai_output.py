"""Unit tests: AI output validation (tests 21-25)"""
import pytest

from app.schemas.audit import AuditOutput, Decision


def test_valid_pass_output():
    output = AuditOutput(
        decision=Decision.PASS,
        summary="All checks passed",
        rules=[],
    )
    assert output.decision == Decision.PASS


def test_valid_reject_output():
    output = AuditOutput(
        decision=Decision.REJECT,
        summary="Failed compliance check",
        rules=[],
    )
    assert output.decision == Decision.REJECT


def test_valid_manual_review_output():
    output = AuditOutput(
        decision=Decision.MANUAL_REVIEW,
        summary="Needs human review",
        manual_review_reasons=["Insufficient data"],
    )
    assert output.decision == Decision.MANUAL_REVIEW


def test_invalid_decision_rejected():
    with pytest.raises(ValueError):
        AuditOutput(decision="INVALID_DECISION", summary="")


def test_default_values():
    output = AuditOutput(decision=Decision.PASS, summary="test")
    assert output.rules == []
    assert output.manual_review_reasons == []
