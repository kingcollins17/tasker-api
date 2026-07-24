# Cascading Dispatch Events & Performance Metrics Guide

## Overview

The **DispatchEventService** ([app/features/tasks/dispatch_service.py](file:///Users/mac/collins/dev/tasker-api/app/features/tasks/dispatch_service.py)) and associated **Celery Tasks** ([app/features/tasks/celery_tasks.py](file:///Users/mac/collins/dev/tasker-api/app/features/tasks/celery_tasks.py)) manage the real-time event lifecycle for on-demand dispatching, provider auto-pause safeguards, task completion counters, and 30-day rolling performance metrics.

---

## 1. Cascading Dispatch Event Workflow

```text
[Task Posted / Searching] ──► DispatchEventService.create_dispatch_attempt()
                                         │
                                         ▼ (30-second window)
                         ┌───────────────┴───────────────┐
                         ▼ ACCEPTED                      ▼ DECLINED / TIMEOUT
             [TaskAssignment Created]         [consecutive_declines += 1]
             [task.status = ASSIGNED]                    │
             [duty_status = ON_TASK]           ┌─────────┴─────────┐
             [consecutive_declines = 0]        ▼                   ▼
                                       (< 3 Declines)     (>= 3 Declines)
                                     [ON_AVAILABLE]     [Auto-Pause Provider]
                                     [Cascade #2]       [is_online = False]
                                                        [duty_status = OFFLINE]
```

---

## 2. Event Triggers & Field Modifications

| Event Trigger | Method / Task | Target Fields & Actions |
| :--- | :--- | :--- |
| **Ping Issued** | `create_dispatch_attempt` | Creates `TaskDispatchAttempt(status=PENDING)`. Updates provider `duty_status = ON_DISPATCH` and task `status = SEARCHING`. Schedules `handle_dispatch_ping_timeout` (30s countdown). |
| **Ping Accepted** | `handle_provider_response(ACCEPTED)` | Sets attempt `status = ACCEPTED`. Sets provider `duty_status = ON_TASK` & resets `consecutive_declines = 0`. Creates `TaskAssignment`. Cancels remaining pending attempts. |
| **Ping Declined / Timed Out** | `handle_provider_response(DECLINED / TIMEOUT)` | Sets attempt `status = DECLINED` or `TIMEOUT`. Increments provider `consecutive_declines += 1`. If `consecutive_declines >= 3`, sets `is_online = False` & `duty_status = OFFLINE` (auto-pause safeguard). |
| **Task Completed** | `complete_task_assignment` | Updates task & assignment status to `COMPLETED`. Increments provider `total_tasks_completed += 1`. Resets `duty_status = ONLINE_AVAILABLE`. Dispatches `recalculate_provider_metrics.delay(provider_id)`. |

---

## 3. Rolling 30-Day Metrics Formulas

Evaluated asynchronously via `@shared_task(name="tasks.recalculate_provider_metrics")`:

### Acceptance Rate 30D

$$\text{acceptance\_rate\_30d} = \left( \frac{\text{Accepted Dispatch Attempts in Last 30 Days}}{\text{Total Dispatch Pings Sent in Last 30 Days}} \right) \times 100$$

*(Default: $100.0\%$ if zero pings recorded).*

### Completion Rate 30D

$$\text{completion\_rate\_30d} = \left( \frac{\text{Completed Tasks in Last 30 Days}}{\text{Total Assigned Tasks in Last 30 Days}} \right) \times 100$$

*(Default: $100.0\%$ if zero assignments recorded).*

---

## 4. Usage Example

```python
from app.features.tasks.dispatch_service import DispatchEventService
from app.features.tasks.celery_tasks import recalculate_provider_metrics

# 1. Dispatch ping attempt to provider
dispatch_service = DispatchEventService(session)
attempt = await dispatch_service.create_dispatch_attempt(
    task_id="task-uuid-123",
    provider_id="provider-uuid-456",
    sequence_order=1,
    offered_payout=9520.0,
    match_score=88.5
)

# 2. Provider accepts ping
success = await dispatch_service.handle_provider_response(
    task_id="task-uuid-123",
    provider_id="provider-uuid-456",
    response_status=DispatchAttemptStatus.ACCEPTED
)

# 3. Provider finishes job on-site
completed = await dispatch_service.complete_task_assignment(
    task_id="task-uuid-123",
    provider_id="provider-uuid-456"
)

# 4. Trigger asynchronous Celery metrics recalculation
recalculate_provider_metrics.delay("provider-uuid-456")
```
