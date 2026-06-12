from celery import Celery

from app.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "arms_worker",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_default_queue="audit_normal",
    task_queues={
        "pdf": {"exchange": "pdf", "routing_key": "pdf"},
        "audit_high": {"exchange": "audit_high", "routing_key": "audit_high"},
        "audit_normal": {"exchange": "audit_normal", "routing_key": "audit_normal"},
        "retry": {"exchange": "retry", "routing_key": "retry"},
    },
    task_routes={
        "app.workers.tasks.process_pdf": {"queue": "pdf"},
        "app.workers.tasks.run_audit_task": {"queue": "audit_normal"},
    },
    task_default_rate_limit=None,
    worker_prefetch_multiplier=1,
    broker_transport_options={"visibility_timeout": 3600},
)
