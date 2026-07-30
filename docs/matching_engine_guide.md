# Comprehensive Guide: Ephemeral Matching Engine (`MatchingEngine.run()`)

This document explains the workflow and inner workings of `MatchingEngine.run()` in simple terms.

---

## High-Level Architecture Overview

The `MatchingEngine` is an **ephemeral service class**. It is instantiated per execution step with a `session_id` and a database session `db_session`.

```text
Task Created / Confirmed
         │
         ▼
Create DispatchSession (status = SEARCHING, current_batch = 1)
         │
         ▼
Schedule execute_matching_engine_task(session_id)
         │
         ▼
Celery Worker Starts
         │
         ▼
engine = MatchingEngine(session_id, db_session)
engine.run()
         │
         ▼
Engine Destroyed
```

If candidates do not accept within 30 seconds:
```text
30 Seconds Later (Celery Countdown Fires)
         │
         ▼
Celery Worker Starts
         │
         ▼
engine = MatchingEngine(session_id, db_session)
engine.run() (advances current_batch = 2)
         │
         ▼
Engine Destroyed
```

---

## Everything `MatchingEngine.run()` Does (Step-by-Step)

### Step 1: Session Verification
- Loads `DispatchSession` from database by `session_id`.
- **Check**: Validates that `session.status == SEARCHING`.
- **Reason**: If another provider already accepted the job or the session was cancelled, `run()` exits immediately without doing work.

### Step 2: Optimistic Concurrency Locking
- Checks the session's `current_batch` number (e.g. batch = 1).
- Executes an atomic SQL `UPDATE`:
  ```sql
  UPDATE dispatch_sessions
  SET current_batch = current_batch + 1, updated_at = NOW()
  WHERE id = :session_id AND current_batch = :batch_num AND status = 'SEARCHING';
  ```
- **Reason**: Prevents race conditions. If two workers run for the exact same session at the same time, only ONE worker succeeds in updating the batch counter (`rowcount == 1`). The loser worker gets `rowcount == 0` and exits cleanly.

### Step 3: Task Validation
- Loads `Task` by `task_id`.
- **Check**: Validates that `task.status == SEARCHING`.
- **Reason**: Halts matching if customer deleted or modified the task.

### Step 4: Candidate Discovery, Ranking & Batch Selection
- **Spatial Discovery (PostGIS)**: Finds all providers whose location is within **10 kilometers** of the task location.
- **Eligibility Filtering**: Ensures candidate providers pass ALL rules:
  1. User account is active (`is_active == True`).
  2. Provider KYC status is verified (`status == VERIFIED`).
  3. Provider is linked to the required `service_id`.
  4. Provider duty status is available (`duty_status == ONLINE_AVAILABLE`).
  5. Provider schedule matches target time (`availability_service`).
- **Multi-Factor Ranking (Quality Score)**: Calculates a composite quality score (0 - 100) using 5 factors:
  - Average Ratings (30%)
  - Total Tasks Completed (20%)
  - Credibility Score (20%)
  - 30-day Acceptance Rate (15%)
  - Distance Proximity (15%)
- **Filter Attempted Candidates**: Excludes providers who have already received a ping attempt in this session.
- **Batch Selection**: Takes top N candidates based on `batch_size` (e.g., top candidate or top 5 candidates).

### Step 5: Exhaustion Handling (Empty Candidate Batch)
If no candidates pass filters or all candidates have already been pinged:
- Marks `DispatchSession.status = EXPIRED`.
- Marks `Task.status = CANCELLED`.
- Sends push & in-app notification to Customer: *"No Providers Available — Task Cancelled"*.
- Exits returning `False`.

### Step 6: Dispatch Pings & Provider Duty Status
For each candidate in the selected batch:
- Creates a `TaskDispatchAttempt` database record (`status = PENDING`, 30s expiration).
- Updates provider `duty_status = ON_DISPATCH` in database.
- Sends high-priority push & in-app notification to Provider: *"New Task Offer — Tap to respond within 30 seconds"*.

### Step 7: Recursive Celery Step Scheduling
- Schedules `execute_matching_engine_task.apply_async(args=[session_id], countdown=30)` with a 30-second delay.
- **Why**: If a provider accepts within 30s, session status changes to `ASSIGNED`. When this 30s timer fires, Step 1 sees `status != SEARCHING` and exits cleanly. If nobody accepts, Step 1 sees `status == SEARCHING` and runs the next batch step!
