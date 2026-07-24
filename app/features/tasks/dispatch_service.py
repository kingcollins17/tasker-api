"""Dispatch event service — stateless proxy over Celery tasks.

All business logic lives in ``app.features.tasks.celery.dispatch``.
This module provides a clean, injectable interface so that routers and
other services never need to import Celery tasks directly.

Usage (FastAPI endpoint)::

    from app.features.tasks.dispatch_service import (
        DispatchEventService,
        get_dispatch_event_service,
    )

    @router.post("/tasks/{task_id}/complete")
    async def complete_task(
        task_id: str,
        current_user: UserResponse = Depends(GetCurrentUser()),
        dispatch: DispatchEventService = Depends(get_dispatch_event_service),
    ):
        dispatch.complete_task_assignment(task_id, current_user.id)
        ...
"""

from app.core.models.tasks import DispatchAttemptStatus
from app.features.tasks.celery.dispatch import (
    complete_task_assignment,
    dispatch_next_candidate,
    process_provider_dispatch_response,
    start_dispatch_workflow,
)


class DispatchEventService:
    """Stateless proxy that forwards all dispatch operations to Celery workers.

    Methods are intentionally synchronous — they merely enqueue a task and
    return immediately.  The actual DB work runs asynchronously in a worker.
    """

    def start_dispatch(self, task_id: str) -> None:
        """Enqueues the first candidate discovery + ping for a newly created task."""
        # pyrefly: ignore [not-callable]
        start_dispatch_workflow.delay(task_id)

    def dispatch_next_candidate(self, task_id: str) -> None:
        """Enqueues selection of the next best candidate and a timed dispatch ping."""
        # pyrefly: ignore [not-callable]
        dispatch_next_candidate.delay(task_id)

    def handle_provider_response(
        self,
        task_id: str,
        provider_id: str,
        response_status: DispatchAttemptStatus,
    ) -> None:
        """Enqueues processing of a provider's accept, decline, or timeout response."""
        # pyrefly: ignore [not-callable]
        process_provider_dispatch_response.delay(
            task_id, provider_id, response_status.value)

    def complete_task_assignment(self, task_id: str, provider_id: str) -> None:
        """Enqueues finalisation of a task (COMPLETED status, provider reset)."""
        # pyrefly: ignore [not-callable]
        complete_task_assignment.delay(task_id, provider_id)


def get_dispatch_event_service() -> DispatchEventService:
    """FastAPI dependency that returns a ``DispatchEventService`` proxy instance."""
    return DispatchEventService()
