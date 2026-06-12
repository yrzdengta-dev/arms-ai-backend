import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.models.audit_result import AuditResult
from app.models.order import Order
from app.models.order_file import OrderFile
from app.models.task_outbox import TaskOutbox
from app.models.user import User


@pytest.mark.asyncio
async def test_pdf_file_queries_are_scoped_to_current_version(db_session):
    from app.workers.tasks import _get_order_pdf_files, _get_pending_files

    user = User(id="hardening-files-user", arms_account="hardening-files")
    order = Order(
        id=str(uuid.uuid4()),
        task_order_id="HARDEN-FILES-1",
        owner_user_id=user.id,
        order_version=2,
        pipeline_status="PDF_QUEUED",
    )
    db_session.add_all([user, order])
    await db_session.flush()
    db_session.add_all([
        OrderFile(
            order_id=order.id,
            order_version=1,
            original_name="old.pdf",
            source_url="https://example.com/old.pdf",
            parse_status="PENDING",
        ),
        OrderFile(
            order_id=order.id,
            order_version=2,
            original_name="current.pdf",
            source_url="https://example.com/current.pdf",
            parse_status="PENDING",
        ),
    ])
    await db_session.flush()

    pending = await _get_pending_files(db_session, order.id, order.order_version)
    all_files = await _get_order_pdf_files(db_session, order.id, order.order_version)

    assert [f["name"] for f in pending] == ["current.pdf"]
    assert [f["name"] for f in all_files] == ["current.pdf"]


@pytest.mark.asyncio
async def test_result_endpoint_does_not_return_prior_version_result(
    db_session, client: AsyncClient,
):
    user = User(id="hardening-result-user", arms_account="hardening-result")
    order = Order(
        id=str(uuid.uuid4()),
        task_order_id="HARDEN-RESULT-1",
        owner_user_id=user.id,
        order_version=2,
        pipeline_status="PDF_QUEUED",
        order_snapshot={"skc": "CURRENT"},
    )
    db_session.add_all([user, order])
    await db_session.flush()
    db_session.add(AuditResult(
        order_id=order.id,
        order_version=1,
        decision="PASS",
        normalized_output={"decision": "PASS", "summary": "old", "rules": []},
    ))
    await db_session.commit()

    response = await client.get(
        f"/api/v1/orders/{order.task_order_id}/result",
        headers={"X-ARMS-User": user.arms_account},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["order_version"] == 2
    assert body["decision"] is None


@pytest.mark.asyncio
async def test_list_and_stats_ignore_prior_version_result(
    db_session, client: AsyncClient,
):
    user = User(id="hardening-list-user", arms_account="hardening-list")
    order = Order(
        id=str(uuid.uuid4()),
        task_order_id="HARDEN-LIST-1",
        owner_user_id=user.id,
        order_version=2,
        pipeline_status="PDF_QUEUED",
        order_snapshot={
            "skc": "SKC-HARDEN",
            "product_name": "Hardening Product",
            "supplier_name": "Hardening Supplier",
            "certificate_type_name": "Hardening Certificate",
        },
    )
    db_session.add_all([user, order])
    await db_session.flush()
    db_session.add(AuditResult(
        order_id=order.id,
        order_version=1,
        decision="REJECT",
        normalized_output={"decision": "REJECT", "summary": "old", "rules": []},
    ))
    await db_session.commit()

    headers = {"X-ARMS-User": user.arms_account}
    list_response = await client.get("/api/v1/orders", headers=headers)
    stats_response = await client.get("/api/v1/orders/stats", headers=headers)

    item = next(i for i in list_response.json()["items"] if i["task_order_id"] == order.task_order_id)
    assert item["decision"] is None
    assert item["supplier_name"] == "Hardening Supplier"
    assert item["certificate_type_name"] == "Hardening Certificate"
    assert stats_response.json()["by_decision"]["PENDING"] == 1


@pytest.mark.asyncio
async def test_same_input_on_new_order_version_creates_new_result(db_session):
    from app.services.audit_service import run_audit

    order = Order(
        task_order_id="HARDEN-IDEMP-1",
        owner_user_id="hardening-idemp-user",
        order_version=1,
        pipeline_status="AI_RUNNING",
        order_snapshot={
            "skc": "SKC-001",
            "scene_id": "7",
            "audit_point_id": "9",
            "certificate_type_id": "1",
        },
    )
    db_session.add(order)
    await db_session.flush()

    first = await run_audit(db_session, order, "same PDF content long enough")
    order.order_version = 2
    await db_session.flush()
    second = await run_audit(db_session, order, "same PDF content long enough")

    assert second.id != first.id
    assert second.order_version == 2


@pytest.mark.asyncio
async def test_stale_dispatched_outbox_is_made_eligible_for_redispatch(db_session):
    from app.services.dispatcher import reconcile_stale_outbox

    user = User(id="hardening-outbox-user", arms_account="hardening-outbox")
    order = Order(
        id=str(uuid.uuid4()),
        task_order_id="HARDEN-OUTBOX-1",
        owner_user_id=user.id,
        order_version=1,
        pipeline_status="PDF_QUEUED",
    )
    record = TaskOutbox(
        order_id=order.id,
        order_version=1,
        task_type="process_pdf",
        task_payload={"order_id": order.id, "order_version": 1},
        dispatched=True,
    )
    db_session.add_all([user, order, record])
    await db_session.commit()

    recovered = await reconcile_stale_outbox(db_session, stale_after_seconds=0)
    await db_session.commit()

    refreshed = await db_session.get(TaskOutbox, record.id)
    assert recovered >= 1
    assert refreshed is not None
    assert refreshed.dispatched is False


@pytest.mark.asyncio
async def test_unknown_outbox_task_is_not_marked_dispatched(db_session):
    from app.services.dispatcher import dispatch_pending_once

    record = TaskOutbox(
        order_id=str(uuid.uuid4()),
        order_version=1,
        task_type="unknown",
        task_payload={},
        dispatched=False,
    )
    db_session.add(record)
    await db_session.commit()

    await dispatch_pending_once(db_session)
    await db_session.commit()

    refreshed = await db_session.get(TaskOutbox, record.id)
    assert refreshed is not None
    assert refreshed.dispatched is False


@pytest.mark.asyncio
async def test_manual_audit_result_transitions_to_manual_required(db_session, monkeypatch):
    from app.adapters.llm.fake_provider import AuditModelResponse
    from app.schemas.audit import AuditOutput, Decision
    from app.workers import tasks

    user = User(id="hardening-manual-user", arms_account="hardening-manual")
    order = Order(
        id=str(uuid.uuid4()),
        task_order_id="HARDEN-MANUAL-1",
        owner_user_id=user.id,
        order_version=1,
        pipeline_status="PDF_READY",
        order_snapshot={
            "skc": "SKC-001",
            "scene_id": "7",
            "audit_point_id": "9",
            "certificate_type_id": "1",
        },
    )
    file_record = OrderFile(
        order_id=order.id,
        order_version=1,
        original_name="ready.pdf",
        source_url="https://example.com/ready.pdf",
        parse_status="READY",
        parsed_text="long enough parsed PDF text",
    )
    db_session.add_all([user, order, file_record])
    await db_session.commit()
    order_id = order.id

    class ManualProvider:
        async def audit(self, request):
            return AuditModelResponse(
                decision=Decision.MANUAL_REVIEW.value,
                raw_output={"decision": "MANUAL_REVIEW"},
                normalized_output=AuditOutput(
                    decision=Decision.MANUAL_REVIEW,
                    summary="manual",
                    rules=[],
                    manual_review_reasons=["provider unavailable"],
                ),
                model_provider="test",
                model_name="test",
                input_hash="test",
            )

    monkeypatch.setattr("app.services.audit_service._get_provider", lambda: ManualProvider())
    await tasks._run_audit_task(order_id, 1)

    db_session.expire_all()
    refreshed = (await db_session.execute(select(Order).where(Order.id == order_id))).scalar_one()
    assert refreshed.pipeline_status == "MANUAL_REQUIRED"


@pytest.mark.asyncio
async def test_reingest_ignores_collector_runtime_metadata(
    client: AsyncClient,
):
    headers = {"X-ARMS-User": "hardening-hash-user"}
    payload = {
        "task_order_id": "HARDEN-HASH-1",
        "scene_id": "7",
        "audit_point_id": "9",
        "order_snapshot": {
            "skc": "SKC-HASH-1",
            "product_name": "Stable product",
            "collected_at": "2026-06-11T01:00:00Z",
            "collection_run_id": "run-one",
        },
        "raw_detail": {
            "aca_task_field_dto": {
                "certificate_url": "https://cdn.example/report.pdf?token=one",
            },
        },
        "pdf_files": [
            {
                "name": "report.pdf",
                "url": "https://cdn.example/report.pdf?token=one",
            },
        ],
    }

    first = await client.post("/api/v1/orders/ingest", json=payload, headers=headers)
    assert first.status_code == 200

    payload["order_snapshot"]["collected_at"] = "2026-06-11T02:00:00Z"
    payload["order_snapshot"]["collection_run_id"] = "run-two"
    payload["raw_detail"]["aca_task_field_dto"]["certificate_url"] = (
        "https://cdn.example/report.pdf?token=two"
    )
    payload["pdf_files"][0]["url"] = "https://cdn.example/report.pdf?token=two"

    second = await client.post("/api/v1/orders/ingest", json=payload, headers=headers)

    assert second.status_code == 200
    assert second.json()["created"] is False
    assert second.json()["order_version"] == 1


@pytest.mark.asyncio
async def test_reingest_material_change_updates_routing_metadata(
    client: AsyncClient,
):
    headers = {"X-ARMS-User": "hardening-routing-user"}
    payload = {
        "task_order_id": "HARDEN-ROUTING-1",
        "task_uuid": "uuid-v1",
        "scene_id": "7",
        "audit_point_id": "9",
        "audit_node": "node-v1",
        "business_type": "type-v1",
        "order_snapshot": {"skc": "SKC-ROUTING-1", "product_name": "Version one"},
        "raw_detail": {},
    }
    first = await client.post("/api/v1/orders/ingest", json=payload, headers=headers)
    assert first.status_code == 200

    payload.update({
        "task_uuid": "uuid-v2",
        "scene_id": "8",
        "audit_point_id": "10",
        "audit_node": "node-v2",
        "business_type": "type-v2",
    })
    payload["order_snapshot"]["product_name"] = "Version two"

    second = await client.post("/api/v1/orders/ingest", json=payload, headers=headers)
    detail = await client.get(
        "/api/v1/orders/HARDEN-ROUTING-1",
        headers=headers,
    )

    assert second.status_code == 200
    assert second.json()["order_version"] == 2
    assert detail.status_code == 200
    assert detail.json()["task_uuid"] == "uuid-v2"
    assert detail.json()["scene_id"] == "8"
    assert detail.json()["audit_point_id"] == "10"
    assert detail.json()["audit_node"] == "node-v2"
    assert detail.json()["business_type"] == "type-v2"
