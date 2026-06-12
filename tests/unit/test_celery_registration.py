"""Unit tests: Celery task registration and task-routes consistency.

RED expectations (before fix):
  - test_celery_app_includes_tasks_module: FAIL (no include)
  - test_task_routes_keys_match_actual_task_names: FAIL (run_audit vs run_audit_task)
  - test_tasks_module_registers_expected_names: PASS (decorators work when imported)
"""

import pytest


class TestCeleryTaskRegistration:
    """Verify celery_app configuration ensures task registration at Worker startup."""

    def test_celery_app_includes_tasks_module(self):
        """celery_app must include 'app.workers.tasks' so the Worker loads
        @celery_app.task decorators. Without this, the Worker discards every
        task as unregistered."""
        from app.workers.celery_app import celery_app

        includes = list(celery_app.conf.get('include') or [])
        imports = list(celery_app.conf.get('imports') or [])

        assert (
            'app.workers.tasks' in includes or 'app.workers.tasks' in imports
        ), (
            "celery_app must include 'app.workers.tasks' so the Worker registers "
            "process_pdf and run_audit_task at startup. "
            f"Current include={includes}, imports={imports}"
        )

    def test_task_routes_keys_match_actual_task_names(self):
        """Every key in task_routes MUST match a task name registered by the
        tasks module.  A route whose key does not match any real task is dead
        config — the task falls through to the default queue, masking the bug."""
        from app.workers.celery_app import celery_app
        import app.workers.tasks  # noqa: F401 — force @celery_app.task side-effects

        routes = celery_app.conf.get('task_routes') or {}
        registered = set(celery_app.tasks.keys())

        assert registered, 'Expected at least one registered task after importing tasks module'

        for route_name in routes:
            assert route_name in registered, (
                f"task_routes key '{route_name}' does not match any registered task. "
                f"Registered: {sorted(registered)}"
            )

    def test_tasks_module_registers_expected_names(self):
        """Importing app.workers.tasks MUST register process_pdf and run_audit_task
        on the celery_app instance (the decorators are the source of truth)."""
        from app.workers.celery_app import celery_app
        import app.workers.tasks  # noqa: F401

        registered = set(celery_app.tasks.keys())

        assert 'app.workers.tasks.process_pdf' in registered, (
            f"process_pdf not registered. Registered: {sorted(registered)}"
        )
        assert 'app.workers.tasks.run_audit_task' in registered, (
            f"run_audit_task not registered. Registered: {sorted(registered)}"
        )
