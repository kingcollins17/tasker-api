# Ratings, Reviews & Credibility Score Architecture

## Overview

After a task is completed, both the **customer** and **provider** can leave a review for each other. Reviews consist of a numeric star rating (1–5) and an optional text comment. Alongside reviews, a **Credibility Ledger** tracks a running score for every user — positive entries for good behaviour, negative entries for bad behaviour — which directly influences provider ranking during task dispatch.

---

## Rating & Review System

### Who Reviews Whom

| Actor | Reviews | Trigger |
|---|---|---|
| Customer | Provider | After task reaches `COMPLETED` status |
| Provider | Customer | After task reaches `COMPLETED` status |

Both reviews are independently submitted. Neither party sees the other's review until both have submitted (double-blind) or after a 48-hour window expires — whichever comes first.

### Review Window

- Reviews can only be submitted while `task.status == COMPLETED`.
- A configurable window of **48 hours** after completion; after that the endpoint rejects new reviews.
- Each user may only submit **one review per task** (enforced via unique constraint on `task_id + reviewer_id`).

### Data Model: `TaskReview`

```python
class TaskReview(SQLModel, table=True):
    __tablename__ = "task_reviews"
    __table_args__ = (UniqueConstraint("task_id", "reviewer_id"),)

    id: str                          # UUID PK
    task_id: str                     # FK → tasks.id
    reviewer_id: str                 # FK → users.id (who wrote the review)
    reviewee_id: str                 # FK → users.id (who is being reviewed)
    rating: int                      # 1–5 star integer
    comment: Optional[str]           # Optional free-text comment
    is_visible: bool = False         # Hidden until double-blind window expires
    created_at: datetime
```

### `User.average_ratings` Recalculation

After each review is submitted, a Celery task `recalculate_user_ratings` is enqueued to recompute the user's `average_ratings` field from all their received `TaskReview` records.

---

## Credibility Ledger

### Purpose

The credibility ledger is an **append-only event log** of score deltas. Every notable event inserts a row. The user's current `credibility_score` is the rolling sum of all their ledger entries, clamped to a range of **0–100**. An initial seed of `25.0` is given at registration.

### Data Model: `CredibilityLedgerEntry`

```python
class CredibilityLedgerEntry(SQLModel, table=True):
    __tablename__ = "credibility_ledger"

    id: str                          # UUID PK
    user_id: str                     # FK → users.id
    delta: float                     # Positive = reward, Negative = penalty
    reason: CredibilityReason        # Enum — event type that caused change
    task_id: Optional[str]           # FK → tasks.id (if event is task-related)
    metadata_info: Optional[dict]    # JSON extra context
    created_at: datetime
```

### `CredibilityReason` Enum — Score Events

| Reason | Delta | Description |
|---|---|---|
| `task_completed` | `+3.0` | Provider completes a task successfully |
| `five_star_review` | `+5.0` | User receives a 5-star rating |
| `four_star_review` | `+2.0` | User receives a 4-star rating |
| `three_star_review` | `+0.0` | Neutral — no change |
| `two_star_review` | `−2.0` | User receives a 2-star rating |
| `one_star_review` | `−5.0` | User receives a 1-star rating |
| `job_declined` | `−1.0` | Provider declines a dispatch ping |
| `job_timeout` | `−1.5` | Provider lets a dispatch ping time out |
| `three_consecutive_declines` | `−5.0` | Provider auto-paused after 3 consecutive declines |
| `task_cancelled_by_provider` | `−3.0` | Provider cancels after accepting |
| `task_cancelled_by_customer` | `−1.0` | Customer cancels after assigning a provider |
| `account_verified` | `+5.0` | KYC verification approved |
| `profile_completed` | `+2.0` | Provider fills out full profile |

### Score Clamping & Syncing

After each ledger insert, a Celery task `sync_user_credibility_score` recomputes the sum and writes it back to `users.credibility_score`, clamped between `0.0` and `100.0`.

```
credibility_score = clamp(SUM(ledger.delta WHERE user_id = X), 0.0, 100.0)
```

---

## Impact on Dispatch Ranking

The existing dispatch scoring formula in `dispatch.py` already includes `credibility_score`:

```python
score = (
    (0.30 * acceptance_rate)
    + (0.25 * avg_rating * 20.0)
    + (0.25 * credibility)      # ← directly used here
    - (0.20 * dist_km)
)
```

Higher credibility → higher dispatch ranking → more job offers. Lower credibility → fewer offers → natural deterrent for bad actors.

---

## Celery Task Architecture

```
task COMPLETED
    │
    ├── [both parties submit reviews]
    │       └── POST /reviews  → store TaskReview
    │                          → insert CredibilityLedgerEntry (rating delta)
    │                          → recalculate_user_ratings.delay(reviewee_id)
    │                          → sync_user_credibility_score.delay(reviewee_id)
    │
    ├── dispatch DECLINED / TIMEOUT
    │       └── insert CredibilityLedgerEntry (job_declined / job_timeout)
    │           → sync_user_credibility_score.delay(provider_id)
    │
    └── complete_task_assignment (existing)
            └── insert CredibilityLedgerEntry (task_completed, provider_id)
                → sync_user_credibility_score.delay(provider_id)
```

### New Celery Tasks

| Task Name | Queue | Trigger | Description |
|---|---|---|---|
| `recalculate_user_ratings` | `tasks` | Review submitted | Recomputes `users.average_ratings` from all received reviews |
| `sync_user_credibility_score` | `tasks` | Any ledger insert | Sums all ledger deltas → writes to `users.credibility_score` |

---

## API Endpoints

| Method | Endpoint | Actor | Description |
|---|---|---|---|
| `POST` | `/api/v1/reviews` | Customer or Provider | Submit a review for a completed task |
| `GET` | `/api/v1/reviews/{task_id}` | Any authenticated | Get reviews for a specific task |
| `GET` | `/api/v1/reviews/user/{user_id}` | Any authenticated | Get all reviews received by a user |
| `GET` | `/api/v1/credibility/ledger` | Self or Admin | View your credibility ledger entries |

---

## Implementation Plan

### Phase 1 — Models
- [x] `TaskReview` in `app/core/models/reviews.py`
- [x] `CredibilityLedgerEntry` + `CredibilityReason` in `app/core/models/credibility.py`

### Phase 2 — Celery Tasks
- [x] `recalculate_user_ratings` in `app/features/reviews/celery/tasks.py`
- [x] `sync_user_credibility_score` in `app/features/reviews/celery/tasks.py`

### Phase 3 — Review Feature
- [x] Schemas, service, router in `app/features/reviews/`

### Phase 4 — Credibility Hooks
- [x] Hook `sync_user_credibility_score` into `complete_task_assignment` (task_completed event)
- [x] Hook into dispatch decline/timeout (job_declined, job_timeout events)
- [x] Hook into review submission (rating-based events)
