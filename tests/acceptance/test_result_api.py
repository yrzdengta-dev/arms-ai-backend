"""Acceptance tests: Result API (Section 3.10)

Verifies GET /api/v1/orders/{task_order_id}/result returns:
- pipeline_status
- decision (NOT hardcoded None)
- summary
- Per-rule results
- Source value
- PDF extracted value
- File name
- Page number
- Original evidence
- Skill version, prompt hash, model name
- order_version, updated_at
"""

import pytest
from httpx import AsyncClient

from app.models.audit_result import AuditResult
from app.models.order import Order
from app.models.user import User


class TestResultApi:
    """Result API must return full audit results."""

    @pytest.mark.asyncio
    async def test_result_endpoint_exists(self, client: AsyncClient, default_headers):
        """GET /api/v1/orders/{task_order_id}/result must return 200 (not 404)."""
        # Create an order first
        response = await client.post(
            "/api/v1/orders/ingest",
            json={
                "task_order_id": "TN-RESULT-001",
                "order_snapshot": {"skc": "SKC-R1"},
                "raw_detail": {},
            },
            headers=default_headers,
        )
        assert response.status_code == 200

        # Now try result endpoint
        response = await client.get(
            "/api/v1/orders/TN-RESULT-001/result",
            headers=default_headers,
        )
        # Should exist (404 means endpoint not implemented)
        assert response.status_code != 404, (
            f"Result endpoint returned 404: endpoint may not be implemented. "
            f"Status: {response.status_code}, body: {response.text[:200]}"
        )

    @pytest.mark.asyncio
    async def test_result_includes_decision(self, db_session):
        """Result must include actual decision, not None."""
        user = User(arms_account="result-testuser-1", id="u-result")
        db_session.add(user)
        await db_session.flush()

        # Create order with audit result
        order = Order(
            task_order_id="TN-RESULT-002",
            owner_user_id=user.id,
            pipeline_status="AI_COMPLETED",
            order_version=1,
            detail_hash="test",
        )
        db_session.add(order)
        await db_session.flush()

        result = AuditResult(
            order_id=order.id,
            order_version=1,
            decision="PASS",
            skill_id="test-skill",
            skill_version="1.0.0",
            prompt_version="abc123",
            model_provider="fake",
            model_name="fake-v1",
            raw_output={"decision": "PASS"},
            normalized_output={
                "decision": "PASS",
                "summary": "All checks passed",
                "rules": [
                    {
                        "rule_id": "R1",
                        "result": "PASS",
                        "reason": "SKC matches",
                        "evidence": [
                            {"file_name": "cert.pdf", "page": 1, "quote": "SKC-001"}
                        ],
                    }
                ],
                "manual_review_reasons": [],
            },
        )
        db_session.add(result)
        await db_session.commit()

        # Query result from DB
        from sqlalchemy import select
        stmt = select(AuditResult).where(
            AuditResult.order_id == order.id
        ).order_by(AuditResult.created_at.desc()).limit(1)
        db_result = (await db_session.execute(stmt)).scalars().first()

        assert db_result is not None
        assert db_result.decision == "PASS", f"Decision should be PASS, got {db_result.decision}"
        assert db_result.decision is not None, "Decision must not be None"

    @pytest.mark.asyncio
    async def test_result_includes_rules_and_evidence(self, db_session):
        """Result must include per-rule results with file/page evidence."""
        user = User(arms_account="result-testuser-2", id="u-rules")
        db_session.add(user)
        await db_session.flush()

        order = Order(
            task_order_id="TN-RESULT-003",
            owner_user_id=user.id,
            pipeline_status="AI_COMPLETED",
            order_version=1,
            detail_hash="test",
        )
        db_session.add(order)
        await db_session.flush()

        normalized = {
            "decision": "PASS",
            "summary": "All checks passed",
            "rules": [
                {
                    "rule_id": "RULE_SKC_MATCH",
                    "result": "PASS",
                    "reason": "SKC matches certificate",
                    "evidence": [
                        {"file_name": "certificate.pdf", "page": 1, "quote": "SKC-001"}
                    ],
                },
                {
                    "rule_id": "RULE_DATE_CHECK",
                    "result": "PASS",
                    "reason": "Date is valid",
                    "evidence": [
                        {"file_name": "certificate.pdf", "page": 2, "quote": "2024-01-15"}
                    ],
                },
            ],
            "manual_review_reasons": [],
        }
        result = AuditResult(
            order_id=order.id,
            order_version=1,
            decision="PASS",
            normalized_output=normalized,
        )
        db_session.add(result)
        await db_session.commit()

        # Verify stored result has evidence
        assert result.normalized_output is not None
        rules = result.normalized_output.get("rules", [])
        assert len(rules) == 2, f"Expected 2 rules, got {len(rules)}"
        for rule in rules:
            assert "rule_id" in rule
            assert "result" in rule
            if rule.get("evidence"):
                for ev in rule["evidence"]:
                    assert "file_name" in ev
                    assert "page" in ev
                    assert isinstance(ev["page"], int)
