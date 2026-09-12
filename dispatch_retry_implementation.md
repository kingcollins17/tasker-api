# Taska Dispatch Retry & Redispatch Plan

## 1. Keep the existing structure

Do NOT add a DispatchRun model.

Use:

Task
  └── DispatchSession
        └── DispatchAttempt

- Task = the job being dispatched
- DispatchSession = one complete dispatch cycle
- DispatchAttempt = one provider offer

Every initial dispatch, automatic retry, or manual redispatch creates a new DispatchSession.

Example:

Task
├── Session 1 (INITIAL)
├── Session 2 (AUTO_RETRY)
└── Session 3 (MANUAL)

---

## 2. Add dispatch state to Task

Add fields similar to:

- dispatch_status
- next_dispatch_at
- dispatch_claimed_at
- auto_dispatch_count
- manual_dispatch_count

Statuses:

READY
DISPATCHING
RETRY_SCHEDULED
MATCHED
AUTO_EXHAUSTED
CANCELLED

Use the existing Task status system where possible instead of creating duplicate states.

Limits should be configurable:

AUTO_DISPATCH_MAX = 5
MANUAL_DISPATCH_MAX = 3

---

## 3. DispatchSession

Add:

- trigger: INITIAL | AUTO_RETRY | MANUAL
- status: RUNNING | MATCHED | NO_MATCH | FAILED | EXHAUSTED
- sequence
- started_at
- completed_at
- reason
- metadata

Add a unique constraint on:

(task_id, sequence)

---

## 4. DispatchAttempt

Keep it responsible only for individual provider offers.

Statuses:

PENDING
OFFERED
ACCEPTED
REJECTED
TIMEOUT
CANCELLED

Do NOT use DispatchAttempt to represent retries.

---

## 5. Separate responsibilities

Create:

DispatchService
DispatchPolicy
MatchingEngine

Responsibilities:

### MatchingEngine
Only:
- find eligible providers
- apply radius/filter rules
- create provider offers
- return the matching result

It should NOT decide:
- retry limits
- retry timing
- automatic vs manual dispatch

### DispatchPolicy
Controls:
- retry limits
- retry delays
- manual limits
- retry decisions

### DispatchService
Controls the dispatch lifecycle and calls MatchingEngine.

---

## 6. Automatic retry

Flow:

INITIAL DISPATCH
    ↓
NO MATCH
    ↓
DispatchPolicy checks retry limit
    ↓
set next_dispatch_at
    ↓
RETRY_SCHEDULED
    ↓
Celery Beat detects due task
    ↓
worker claims task
    ↓
new DispatchSession(AUTO_RETRY)
    ↓
MatchingEngine

Do NOT keep a Celery worker sleeping while waiting for the retry.

Example delays:

2m → 5m → 10m → 20m → 30m

Make these configurable.

---

## 7. Celery architecture

Use:

Celery Beat
    ↓
process_due_dispatches
    ↓
claim due tasks in batches
    ↓
DispatchService

Beat should only schedule the job.

Workers perform the actual dispatching.

For claiming tasks, use PostgreSQL:

SELECT ...
FROM task
WHERE dispatch_status = 'RETRY_SCHEDULED'
  AND next_dispatch_at <= NOW()
ORDER BY next_dispatch_at
LIMIT :batch_size
FOR UPDATE SKIP LOCKED;

Immediately mark claimed tasks as DISPATCHING and commit.

Do NOT hold database locks while MatchingEngine is running.

---

## 8. Large backlogs

Never load hundreds of thousands of tasks into memory.

Use batching:

BATCH_SIZE = 500
MAX_BATCHES_PER_RUN = 20

Process a limited number of batches per scheduler cycle.

This prevents Redis/Celery from being flooded with hundreds of thousands of individual jobs.

---

## 9. Prevent provider reuse

When finding candidates, exclude providers already attempted for the task where appropriate.

For example:

- REJECTED → normally exclude
- TIMEOUT → business-dependent
- CANCELLED → business-dependent

Query previous DispatchAttempts through DispatchSession.task_id.

---

## 10. Manual redispatch

Endpoint:

POST /tasks/{task_id}/redispatch

Flow:

API
 → DispatchService.manual_dispatch()
 → check manual limit
 → create DispatchSession(MANUAL)
 → MatchingEngine
 → update task/session

Automatic and manual limits are independent.

Example:

5 automatic attempts + 3 manual redispatches.

---

## 11. Concurrency protection

Automatic retry and manual redispatch could happen at the same time.

Before creating a dispatch session:

1. Lock the task row.
2. Check task state.
3. Check limits.
4. Increment the appropriate counter.
5. Create DispatchSession.
6. Set DISPATCHING.
7. Commit.

Release the lock before running MatchingEngine.

Keep the transaction short.

Existing lock_version can remain if already used.

---

## 12. Crash recovery

If a worker crashes after marking a task DISPATCHING, it must not remain stuck forever.

Use dispatch_claimed_at.

Periodically recover stale claims:

DISPATCHING + claimed_at older than configured timeout
    → RETRY_SCHEDULED
    → next_dispatch_at = NOW()

---

## 13. Indexes

Add:

- Task.next_dispatch_at partial index for RETRY_SCHEDULED
- DispatchSession.task_id
- DispatchAttempt.dispatch_session_id
- DispatchAttempt.provider_id

Important index:

CREATE INDEX ix_task_due_dispatch
ON task (next_dispatch_at)
WHERE dispatch_status = 'RETRY_SCHEDULED';

---

## 14. Audit/observability

Every dispatch cycle should be traceable.

Log/store:

- task_id
- dispatch_session_id
- trigger
- sequence
- candidate_count
- attempt_count
- result
- reason
- retry number
- duration
- next_dispatch_at

Optional DispatchEvent table can store events such as:

TASK_DISPATCH_STARTED
NO_MATCH
PROVIDER_OFFERED
PROVIDER_REJECTED
PROVIDER_TIMEOUT
PROVIDER_ACCEPTED
AUTO_RETRY_SCHEDULED
AUTO_RETRY_EXHAUSTED
MANUAL_REDISPATCH
DISPATCH_FAILED

---

## 15. Implementation order

1. Add Task dispatch fields.
2. Update DispatchSession model.
3. Add/verify DispatchAttempt fields.
4. Create DispatchPolicy.
5. Create/refactor DispatchService.
6. Make MatchingEngine matching-only.
7. Implement automatic retry scheduling.
8. Add Celery Beat + batched due-task processing.
9. Add concurrency protection.
10. Add stale-claim recovery.
11. Add manual redispatch endpoint.
12. Add indexes and audit logging.

## Core rule

DispatchSession = one dispatch cycle.

DispatchAttempt = one provider offer.

Every retry/redispatch = new DispatchSession.

MatchingEngine = matching only.

DispatchService = lifecycle.

DispatchPolicy = limits/timing.

Celery Beat = scheduler.

Celery Worker = execution.

PostgreSQL = source of truth for claiming due tasks.