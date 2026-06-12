"""Unit tests: AI idempotency enforcement (Category J)

Verifies:
- compute_audit_input_hash is deterministic
- Different inputs produce different hashes
- run_audit checks for existing AuditResult with same input_hash before calling AI
"""

import asyncio

import pytest
from sqlalchemy import select

from app.models.audit_result import AuditResult
from app.schemas.audit import AuditOutput, Decision


class TestInputHashComputation:
    """Verify input_hash is deterministic and responds to input changes."""

    def test_same_input_produces_same_hash(self):
        """Same prompt, order_snapshot, pdf_text produces same hash."""
        from app.services.audit_service import compute_audit_input_hash

        h1 = compute_audit_input_hash("prompt", {"skc": "X"}, "pdf text")
        h2 = compute_audit_input_hash("prompt", {"skc": "X"}, "pdf text")
        assert h1 == h2
        assert len(h1) == 64  # SHA-256 hex

    def test_different_prompt_produces_different_hash(self):
        from app.services.audit_service import compute_audit_input_hash

        h1 = compute_audit_input_hash("prompt A", {"skc": "X"}, "pdf")
        h2 = compute_audit_input_hash("prompt B", {"skc": "X"}, "pdf")
        assert h1 != h2

    def test_different_snapshot_produces_different_hash(self):
        from app.services.audit_service import compute_audit_input_hash

        h1 = compute_audit_input_hash("p", {"skc": "A"}, "pdf")
        h2 = compute_audit_input_hash("p", {"skc": "B"}, "pdf")
        assert h1 != h2

    def test_different_pdf_text_produces_different_hash(self):
        from app.services.audit_service import compute_audit_input_hash

        h1 = compute_audit_input_hash("p", {"skc": "X"}, "pdf A")
        h2 = compute_audit_input_hash("p", {"skc": "X"}, "pdf B")
        assert h1 != h2

    def test_empty_pdf_text_handled(self):
        """Empty pdf_text should still produce a valid hash."""
        from app.services.audit_service import compute_audit_input_hash

        h = compute_audit_input_hash("prompt", {}, "")
        assert len(h) == 64

    def test_dict_key_order_does_not_matter(self):
        """Hash should be stable regardless of dict insertion order."""
        from app.services.audit_service import compute_audit_input_hash

        h1 = compute_audit_input_hash("p", {"a": "1", "b": "2"}, "pdf")
        h2 = compute_audit_input_hash("p", {"b": "2", "a": "1"}, "pdf")
        assert h1 == h2


class TestAuditIdempotencyInRunAudit:
    """Verify run_audit returns cached result when input_hash matches."""

    @pytest.mark.asyncio
    async def test_duplicate_input_returns_cached_result(self, db_session):
        """Second run_audit with same input should reuse existing result."""
        from app.models.order import Order
        from app.services.audit_service import compute_audit_input_hash, run_audit

        order = Order(
            owner_user_id="user-idemp-1",
            task_order_id="TASK-IDEMP-001",
            order_version=1,
            pipeline_status="AI_RUNNING",
            order_snapshot={
                "skc": "SKC-001",
                "scene_id": "7",
                "audit_point_id": "9",
                "certificate_type_id": "1",
            },
            business_type=None,
        )
        db_session.add(order)
        await db_session.flush()

        # First call — should invoke AI (FakeProvider)
        result1 = await run_audit(db_session, order, pdf_text="PDF content for idempotency test")

        # Second call with same inputs — should return cached result
        result2 = await run_audit(db_session, order, pdf_text="PDF content for idempotency test")

        assert result1.id == result2.id, (
            "Same input should return the cached AuditResult, not create a new one"
        )
        assert result1.input_hash == result2.input_hash

    @pytest.mark.asyncio
    async def test_different_input_creates_new_result(self, db_session):
        """Different order_version + different input create separate results."""
        from app.models.order import Order
        from app.services.audit_service import run_audit

        order = Order(
            owner_user_id="user-idemp-2",
            task_order_id="TASK-IDEMP-002",
            order_version=1,
            pipeline_status="AI_RUNNING",
            order_snapshot={
                "skc": "SKC-002",
                "scene_id": "7",
                "audit_point_id": "9",
                "certificate_type_id": "1",
            },
            business_type=None,
        )
        db_session.add(order)
        await db_session.flush()

        result1 = await run_audit(db_session, order, pdf_text="PDF content A")

        # Change order_version so UNIQUE constraint on (order_id, order_version) allows new row
        order.order_version = 2
        await db_session.flush()

        result2 = await run_audit(db_session, order, pdf_text="PDF content B completely different")

        assert result1.id != result2.id, (
            "Different inputs should create different AuditResults"
        )
        assert result1.input_hash != result2.input_hash

    @pytest.mark.asyncio
    async def test_input_hash_stored_on_result(self, db_session):
        """AuditResult must have input_hash set after audit."""
        from app.models.order import Order
        from app.services.audit_service import run_audit

        order = Order(
            owner_user_id="user-idemp-3",
            task_order_id="TASK-IDEMP-003",
            order_version=1,
            pipeline_status="AI_RUNNING",
            order_snapshot={
                "skc": "SKC-003",
                "scene_id": "7",
                "audit_point_id": "9",
                "certificate_type_id": "1",
            },
            business_type=None,
        )
        db_session.add(order)
        await db_session.flush()

        result = await run_audit(db_session, order, pdf_text="Some PDF text for hash test")
        assert result.input_hash is not None
        assert len(result.input_hash) == 64
