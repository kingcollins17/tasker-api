# Tasker: Product Requirements & Technical Architecture Document V21

**Date:** May 2026  
**Target Stack:** FastAPI (Backend), Flutter (Mobile Clients), Jaspr (Web Admin)  
**Payment Strategy:** Paystack Split Payments & Custom Transaction Escrow  
**Line Spacing:** 1.15  

This document outlines the strategic vision, marketplace dynamics, core engineering parameters, and robust security protocols required to build and deploy Tasker—a high-trust, two-sided blue-collar service marketplace optimized for localized economies.

---

## 1. Product Overview & Unit Economics

### The Problem
Finding trusted, skilled local service providers (plumbers, cleaners, shoppers) remains a fragmented and highly risky process. Customers suffer from unpredictable pricing, unverified professionals, and personal safety anxieties. Conversely, honest, skilled service providers struggle to source quality leads, find regular work, and secure fair, reliable payment timelines without predatory agency fees.

### The Solution: Tasker
Tasker is a decentralized service marketplace operating a high-intent, two-sided transaction platform. By using a Bidding/Quote Model, Tasker democratizes localized labor markets, giving customers competitive transparency and allowing providers to self-price based on exact job complexity. Trust is enforced structurally through programmatic payment escrow and deep, biometric digital identity verification.

### The Business Model
Tasker avoids complex wallet infrastructures and charges directly via processing commissions. Instead of a uniform platform take-rate, commissions are handled dynamically based on category risk parameters, supply density, and transaction volume. The specific percentage is stored directly within each marketplace skill record to automate financial splitting calculations.

### Financial Growth Estimates & Unit Economics (3-Year Projection)
Projections are built on a conservative baseline unit economics model optimized for major urban metropolitan hubs (e.g., Lagos, Abuja) assuming an average baseline take-rate of 12.5%:
*   **Average Task Value (GTV per Task):** ₦25,000
*   **Platform Net Earnings per Task:** ₦3,125

| Metric | Year 1 (Launch & Fit) | Year 2 (Regional Scaling) | Year 3 (National Dominance) |
| :--- | :--- | :--- | :--- |
| **Monthly End-State Run Rate** | ~650 tasks/mo | ~3,000 tasks/mo | ~14,000 tasks/mo |
| **Total Annual Completed Tasks** | ~4,200 | ~20,000 | ~90,000 |
| **Gross Transaction Volume (GTV)** | ~₦105,000,000 | ~₦500,000,000 | ~₦2,250,000,000 |
| **Estimated Platform Revenue** | ~₦13,125,000 | ~₦62,500,000 | ~₦281,250,000 |

### Solving the Chicken-and-Egg Problem (Liquidity Strategy)
To overcome the cold-start problem inherent in two-sided marketplaces, Tasker implements a structured, localized sequence to guarantee market liquidity:
1.  **Supply-First Geographic Micro-Density:** Growth isolates a single high-intent, affluent community (e.g., a 5km radius in Lekki or Ikeja). Tasker onboards and physically verifies a baseline of 300 to 500 "Doers" in that micro-zone before launching the Seeker app.
2.  **The "Staged Task" Seed Strategy:** During the first 60 days, the operations team actively scans community boards or partners with corporate facility management entities to guarantee a baseline of consistent, high-paying jobs on the app.
3.  **High-Trust Identity Subsidies:** To incentivize early Doer adoption despite strict Tier 3 KYC and biometric liveness checks, the platform subsidizes the third-party verification costs for the first 1,000 verified service providers.

---

## 2. Client Interface Architecture

To maximize operational isolation, optimize app store acquisition pipelines, and maintain lightweight architectures, Tasker is split into three core interfaces built around a centralized asynchronous API.

### Tasker Seeker App (Flutter)
*   Mobile OTP / Identity Onboarding
*   Geolocated Task Creation Wizard
*   Multi-Media Task Attachments
*   Bid Evaluation Engine
*   Paystack Direct Checkout
*   Secure WebSockets Chat Console
*   Dynamic PIN Authentication (Start/End)
*   Provider Rating Interface

### Tasker Doer App (Flutter)
*   Tier 3 KYC & Biometric Registry Sign-up
*   Bank Settlement Registration via Paystack
*   Localized Lead Discovery Board
*   Granular Quoting/Bidding Console
*   Obfuscated Geofencing Distance Maps
*   Real-time Bid Modification Dashboard
*   In-app Chat Framework
*   Operational PIN Entry Verification

### Tasker Operations Suite (Jaspr Web)
*   Provider Vetting & Validation Queue
*   Comprehensive Live Task Activity Monitor
*   Paystack Escrow Disbursal Overrides
*   Customer / Provider Dispute Workflows
*   System Configuration & Category Toggles
*   Real-time Security and SOS Logging
*   Anomalous Activity Flagging Systems

### Task Lifecycle State Machine
To avoid race conditions and protect marketplace transactional integrity, the database enforces an atomic, strict state machine for all tasks:
*   **DRAFT:** Task is constructed by the Seeker but not yet broadcasting.
*   **OPEN:** Broadcasted to nearby verified Doers. Bidding is active.
*   **BIDS_UNDER_REVIEW:** One or more bids submitted; Seeker evaluating.
*   **ACCEPTED:** Seeker selects a specific quote. Money is successfully swept to Paystack escrow. Precise location details are released to the chosen provider.
*   **IN_PROGRESS:** Doer arrives on-site and inputs the Seeker-generated "Start PIN".
*   **COMPLETED:** Service finalized, confirmed via "Completion PIN" entry. Transaction settled out of escrow.
*   **DISPUTED:** Flagged by either party. Escrow locked pending manual operational triage.

---

## 3. User Registration & Core Auth Pipeline

*   **Decoupled Client Architecture:** Seeker and Doer apps are developed and compiled as two entirely separate Flutter codebases. This avoids a shared "switch mode" design, lowering binary complexity and completely separating user permissions at the compile level.
*   **Passwordless OTP Verification:** Fast mobile verification via SMS gateways (Termii, Twilio) paired with Redis sliding-window rate-limiting keys (`rate_limit:otp:{phone_number}`) to secure against payload/SMS financial exploits.

### Marketplace Authentication & Category Database Models

```python
import enum
from datetime import datetime
from uuid import uuid4
from typing import List, Optional
from sqlmodel import Field, SQLModel, Relationship

class UserRole(str, enum.Enum):
    SEEKER = "seeker"
    DOER = "doer"

class KYCStatus(str, enum.Enum):
    PENDING_SUBMISSION = "pending_submission"
    SUBMITTED = "submitted"
    PENDING_ADMIN_REVIEW = "pending_admin_review"
    VERIFIED = "verified"
    FAILED = "failed"

class DoerCategoryLink(SQLModel, table=True):
    doer_profile_id: str = Field(foreign_key="doerkycprofile.id", primary_key=True)
    category_id: str = Field(foreign_key="marketcategory.id", primary_key=True)

class MarketCategory(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    name: str = Field(unique=True, index=True) # e.g., "plumber", "mechanic"
    take_rate: float = Field(default=0.10, description="Dynamic percentage take-rate specific to this category")
    is_active: bool = Field(default=True)
    
    profiles: List["DoerKYCProfile"] = Relationship(
        back_populates="categories", link_model=DoerCategoryLink
    )

class UserAuth(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    phone_number: str = Field(unique=True, index=True)
    email: str = Field(unique=True, index=True)
    role: UserRole
    is_active: bool = Field(default=False)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    kyc_profile: Optional["DoerKYCProfile"] = Relationship(back_populates="user")
    payment_accounts: List["PaymentAccount"] = Relationship(
        back_populates="user",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"}
    )
```

---

## 4. Tier 3 KYC Asynchronous Processing & Document Ingestion

*   **Asynchronous Task Offloading:** Upstream checks (Smile ID/Prembly) run inside a background worker pool to prevent HTTP request-worker starvation.
*   **Multi-Category Specialization Matching:** Allows Doers to opt into multiple skill clusters. The task feed inner-joins across `DoerCategoryLink` and filters tasks based directly on the provider's active domain choices.
*   **Secure Portfolio and Resume Upload Tracking:** Verifies files out-of-band for `application/pdf` content types under a strict 5MB barrier, indexing filenames securely within an unindexed private AWS S3 architecture.

### Doer KYC Profile Model Update

```python
from datetime import timezone
from sqlalchemy import Column, JSON

class PaymentProvider(str, enum.Enum):
    PAYSTACK = "paystack"
    MONNIFY = "monnify"
    FLUTTERWAVE = "flutterwave"

class DoerKYCProfile(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    user_id: str = Field(foreign_key="userauth.id", unique=True)
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    id_type: Optional[str] = None # 'NIN', 'BVN'
    id_number: Optional[str] = None
    selfie_s3_url: Optional[str] = None
    
    # Portfolio Track
    resume_s3_url: Optional[str] = Field(default=None)
    resume_uploaded_at: Optional[datetime] = Field(default=None)
    
    status: KYCStatus = Field(default=KYCStatus.PENDING_SUBMISSION)
    provider_reference: Optional[str] = Field(default=None, index=True)
    liveness_score: Optional[float] = None
    rejection_reason: Optional[str] = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    verified_at: Optional[datetime] = None
    address_line: Optional[str] = None

    user: UserAuth = Relationship(back_populates="kyc_profile")
    categories: List[MarketCategory] = Relationship(
        back_populates="profiles", link_model=DoerCategoryLink
    )

class PaymentAccount(SQLModel, table=True):
    __tablename__ = "payment_accounts"
    
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    user_id: str = Field(
        foreign_key="userauth.id",
        index=True
    )
    provider: PaymentProvider
    external_account_id: str
    account_name: Optional[str] = None
    is_active: bool = Field(default=True)
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    account_metadata: dict = Field(
        default_factory=dict,
        sa_column=Column(JSON)
    )
    
    user: UserAuth = Relationship(back_populates="payment_accounts")
```

---

## 5. Task Posting & Real-Time Bidding Architecture

*   **Geographic Broadcast Distribution:** When a task is updated to `OPEN`, the engine performs a PostGIS radius evaluation matching nearby specialists and broadcasts parameters dynamically using integrated Redis Pub/Sub channels connected to persistent application WebSockets.
*   **Atomic Transaction Guards:** Database rows are strictly isolated using raw row locks (`with_for_update()`) during quote evaluation phases to eliminate concurrent transaction conflicts or split race conditions.
*   **Hardware Independent PIN Challenge Verification:** Locks physical confirmation records into place via isolated security tokens. Workflows validate active state paths across strict Start PIN and Completion PIN processing steps.
*   **Lump-Sum Proxy Purchase Model (Errands/Groceries):** For tasks that involve purchasing goods (e.g., grocery runs), the system sets a mandatory boolean `is_external_purchase` flag to `True` and records the client’s `estimated_item_budget`. When a Doer places an all-inclusive bid (labor + item cost), the platform's dynamic commission engine isolates the implied labor portion by subtracting the item budget first, ensuring platform commission fees only tax the actual service labor component.
*   **Task Scheduling Expiration Thresholds:** Includes a mandatory indexing column tracking the `deadline_timestamp` threshold. Tasks failing to transition into operational field activation execution before reaching this marker are pulled from visibility channels automatically.

### Posting & Bidding Database Domain Models

```python
class TaskStatus(str, enum.Enum):
    DRAFT = "draft"
    OPEN = "open"
    BIDS_UNDER_REVIEW = "bids_under_review"
    ACCEPTED = "accepted"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    DISPUTED = "disputed"
    CANCELLED = "cancelled"

class BidStatus(str, enum.Enum):
    PENDING = "pending"
    REJECTED = "rejected"
    ACCEPTED = "accepted"
    RETRACTED = "retracted"

class Task(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    seeker_id: str = Field(foreign_key="userauth.id", index=True)
    category_id: str = Field(foreign_key="marketcategory.id", index=True)
    
    title: str
    description: str
    
    # Multi-Waypoint Structural Tracking
    pickup_latitude: float
    pickup_longitude: float
    pickup_formatted_address: str
    
    destination_latitude: float
    destination_longitude: float
    destination_formatted_address: str
    
    status: TaskStatus = Field(default=TaskStatus.OPEN, index=True)
    budget_range_min: Optional[float] = None
    budget_range_max: Optional[float] = None
    
    # Task Category Feature Flags
    is_delivery_task: bool = Field(default=False, index=True)
    is_external_purchase: bool = Field(default=False, index=True, description="True for tasks requiring item purchases like groceries")
    estimated_item_budget: Optional[float] = Field(default=0.0, description="Anchor cost for the physical goods being bought")
    
    # Task Scheduling Boundaries
    deadline_timestamp: datetime = Field(..., index=True, description="Hard cutoff for task start")
    cancelled_at: Optional[datetime] = Field(default=None)
    cancellation_reason: Optional[str] = Field(default=None)
    
    start_pin: str = Field(default=None)
    completion_pin: str = Field(default=None)
    
    created_at: datetime = Field(default_factory=datetime.utcnow)
    accepted_bid_id: Optional[str] = Field(default=None)

    bids: List["Bid"] = Relationship(back_populates="task")

class Bid(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    task_id: str = Field(foreign_key="task.id", index=True)
    doer_id: str = Field(foreign_key="userauth.id", index=True)
    
    amount: float = Field(..., description="The all-inclusive bid amount containing labor + item cost if applicable")
    estimated_duration_hours: int
    cover_note: Optional[str] = None
    status: BidStatus = Field(default=BidStatus.PENDING, index=True)
    
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    task: Task = Relationship(back_populates="bids")
```

---

## 6. Task Dispute, Escrow Triage & Expiration Engine

*   **Automated Pre-Arrival Recovery Loop:** If a Doer drops a job before arriving on-site, the backend moves the task state seamlessly back to `OPEN` while leaving the escrow safely held in the platform's pool.
*   **Mid-Task Escalation Lockdowns:** Unilateral cancellations are completely disabled once work has started. Initiating a dispute changes the task status to `DISPUTED` and freezes the transaction to await administrative triage.
*   **Manual Splitting Resolutions:** Supports three exact terminal choices on the operator suite dashboard: `FULL_REFUND`, `FULL_PAYOUT`, or an arbitrary `PARTIAL_SPLIT` settlement based on field findings.
*   **Asynchronous Deadline Expiration Engine (Celery Beat / Cron Poller):** An asynchronous background loop polls the data cluster every 60 seconds looking for outstanding tasks that have breached their `deadline_timestamp` boundaries. If an accepted task has reached its expiration cutoff without being initialized, the worker transitions the row to `CANCELLED`, executes a Paystack transaction reversal directly back to the Seeker's card, dispatches system inbox notifications, and records an automated penalty log entry inside the unchangeable behavioral ledger.

### Dispute Table Schema

```python
class DisputeResolutionEnum(str, enum.Enum):
    FULL_REFUND = "full_refund"     # 100% back to Seeker
    FULL_PAYOUT = "full_payout"     # 100% split to Doer
    PARTIAL_SPLIT = "partial_split" # Manual operational split intervention

class TicketStatus(str, enum.Enum):
    OPEN = "open"
    INVESTIGATING = "investigating"
    RESOLVED = "resolved"

class DisputeTicket(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    task_id: str = Field(foreign_key="task.id", unique=True, index=True)
    creator_id: str = Field(foreign_key="userauth.id")
    
    reason_category: str # "damage", "abandonment", "pricing_dispute"
    text_explanation: str
    status: TicketStatus = Field(default=TicketStatus.OPEN, index=True)
    
    resolved_by_admin_id: Optional[str] = Field(default=None, foreign_key="adminuser.id")
    resolution_type: Optional[DisputeResolutionEnum] = None
    admin_final_notes: Optional[str] = None
    resolved_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
```

---

## 7. Programmatic Escrow & Split-Payment Infrastructure (Paystack)

*   **Managed Programmatic Escrow Vaulting:** Avoids regulatory digital wallet overhead by treating Paystack's transaction ledger as an automated escrow repository. Funds are swept upfront upon bid selection and frozen until physical waypoint criteria match.
*   **Dynamic Split Fee Aggregation:** Payout logic splits transactions directly in Kobo units (integers). The system isolates the item budget out-of-band, assigning the baseline dynamic category platform fee entirely to the labor value to avoid unfair double taxation on the provider.
*   **Cryptographic Webhook Verification Firewall:** Validates incoming payloads on `POST /api/v1/callbacks/paystack` against timing exploits and spoof profiles using strict HMAC-SHA512 header verification algorithms backed by system environment secret keys.

---

## 8. Immutable Behavioral Metrics Ledger & Account Lock Subsystem

*   **Log-Only Structure:** Removes mutable rating floats. The score is computed strictly from an immutable stream of logged behaviors.
*   **Database-Level Sum Accumulation:** Employs optimized SQL queries using `func.sum()` to aggregate metrics with an enforced structural ceiling cap of `100.0`.
*   **Write-Driven Redis Eviction:** Caches calculations in Redis keys and completely flushes them the instant a new log entry is committed.
*   **Time-Delayed Feed Prioritization:** Applies progressive visibility delays to the task feed query based directly on the derived score, rewarding top-tier providers with priority access to leads.
*   **Multi-Layered Enforcement Lockout Strategy:** If a calculated reputation score drops below the `70.0` threshold, the system automatically marks `UserAuth.is_active = False` in PostgreSQL, flags the account as `PENDING_ADMIN_REVIEW`, and commits the user ID to an in-memory Redis blacklist key. This blacklisted key is verified via middleware on every incoming router request to force instant JWT eviction.

### Reputation Ledger Schema

```python
class MetricEventType(str, enum.Enum):
    BASELINE_INITIALIZATION = "baseline_initialization"
    LATE_ARRIVAL = "late_arrival"       
    TASK_ABANDONMENT = "task_abandonment" 
    DISPUTE_LOSS = "dispute_loss"       
    CUSTOMER_FIVE_STAR = "customer_five_star" 
    STREAK_BONUS = "streak_bonus"       
    ADMIN_MANUAL_ADJUSTMENT = "admin_manual_adjustment" 

class DoerReputationLedger(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    doer_profile_id: str = Field(foreign_key="doerkycprofile.id", index=True)
    task_id: Optional[str] = Field(default=None, foreign_key="task.id", nullable=True)
    admin_id: Optional[str] = Field(default=None, foreign_key="adminuser.id", nullable=True)
    
    event_type: MetricEventType = Field(..., index=True)
    score_delta: float = Field(..., description="e.g., -10.0 or +1.5")
    justification_notes: Optional[str] = Field(default=None)
    recorded_at: datetime = Field(default_factory=datetime.utcnow, index=True)
```

---

## 9. Two-Way Feedback Review Ledger & Seeker Reputation Engine

*   **Mutual Feedback Enforcement Loop:** Protects supply-side integrity by requiring double-blind peer reviews. Ratings and textual public notes remain hidden until both parties submit feedback or the 7-day post-completion ledger window closes.
*   **Immutable Review Metrics Storage:** Ratings are saved directly inside an unchangeable `ReviewLedger` matrix. Running stars values are dynamically derived via clean database execution commands rather than fragile column rewrites.
*   **Seeker Strategic Access Throttling:** Low customer ratings directly degrade an absolute Seeker reputation score (starting baseline at `100.0`). Scoring dips trigger tier restrictions automatically: dropping below `85.0` limits transactions to pre-paid Paystack escrow; dropping below `75.0` enforces a strict restriction allowing only 1 active task listing; dropping below `65.0` triggers an automated device lockout.

### Two-Way Review Ledger Database Model

```python
class ReviewAuthorRole(str, enum.Enum):
    SEEKER_TO_DOER = "seeker_to_doer"
    DOER_TO_SEEKER = "doer_to_seeker"

class ReviewLedger(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    task_id: str = Field(foreign_key="task.id", index=True)
    author_id: str = Field(foreign_key="userauth.id")
    recipient_id: str = Field(foreign_key="userauth.id", index=True)
    direction: ReviewAuthorRole = Field(..., index=True)
    
    rating_stars: int = Field(..., description="Integer from 1 to 5")
    public_comment: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
```

---

## 10. Hybrid Real-Time Chat & Notification Subsystem

*   **Dual-Channel Pipeline:** Splits traffic based on app visibility state to optimize system overhead and mobile device battery consumption.
*   **Foreground WebSockets Loop:** Employs duplex persistent connections via FastAPI connection managers backed by Redis Pub/Sub channels to distribute sub-50ms chats, real-time quotes, and instant state switches when both clients are active.
*   **Asynchronous Background FCM Fallback:** If a WebSocket channel drops or logs out, a background broker intercepts the transaction queue and wraps data payloads inside high-priority Firebase Cloud Messaging configurations.

### Real-Time Chat & Device Mapping Schemas

```python
class DirectMessage(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    task_id: str = Field(foreign_key="task.id", index=True)
    sender_id: str = Field(foreign_key="userauth.id")
    receiver_id: str = Field(foreign_key="userauth.id")
    
    message_text: str
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)

class UserDeviceToken(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    user_id: str = Field(foreign_key="userauth.id", unique=True, index=True)
    fcm_token: str = Field(..., description="The push notification handle fetched from Flutter SDK")
    device_os: str # "ios" or "android"
    updated_at: datetime = Field(default_factory=datetime.utcnow)
```

---

## 11. Event-Driven Non-Chat Notification Engine

*   **Decoupled Event Ingestion:** Core state transactions (e.g., bid received, escrow cleared, account lockout) fire asynchronous metadata payloads to a central Redis Pub/Sub broadcast channel rather than compiling push code inside business routers.
*   **Persistent User Notification Center:** Supplying a durable database inbox layout allowing consumers to reference historical alert logs and deep links inside their Flutter user profiles even if they missed structural native background push handles.
*   **Dynamic Data Deep Linking:** Payload structures carry explicit deep-linking keys (`metadata_json`) consumed by the mobile repository route maps to instantly pop targeted feature stacks upon system tray interaction.

### Notification Inbox Ledger Schema

```python
class SystemNotificationType(str, enum.Enum):
    BID_RECEIVED = "bid_received"
    BID_ACCEPTED = "bid_accepted"
    PAYMENT_CONFIRMED = "payment_confirmed"
    TASK_STARTED = "task_started"
    ACCOUNT_LOCKOUT = "account_lockout"
    DISPUTE_LOGGED = "dispute_logged"

class SystemNotificationInbox(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    user_id: str = Field(foreign_key="userauth.id", index=True)
    
    notification_type: SystemNotificationType = Field(..., index=True)
    title: str
    body: str
    metadata_json: str = Field(default="{}")
    is_read: bool = Field(default=False, index=True)
    created_at: datetime = Field(default_factory=datetime.utcnow, index=True)
```

---

## 12. Marketplace Supply-Bootstrapping & Growth Liquidity Blueprint

*   **Hyper-Local Supply Isolation:** Prioritizes geographic micro-density within targeted 5km neighborhood hubs (e.g., Lekki, Ikeja) to concentrate provider availability before consumer app launch sequences.
*   **The Staged Task Seed Strategy:** Employs internally funded, pre-seeded operational job boards to create instant platform transaction utility and prevent provider churn on Day 1.
*   **Value-Add Secondary Tooling Utilities:** Off-channel feature vectors allow early onboarding Doers to generate professional PDF quotes and invoices for pre-existing offline clients, utilizing Tasker's standalone Paystack escrow pipes to mitigate private transaction collection risk.

---

## 13. Isolated Operator Control Domain

*   **Privilege De-escalation Isolation:** Administrative and support data footprints are fully separated from standard user tracks. This design fully eliminates perimeter pollution risks.
*   **Audit Compliance Engine:** Logs every critical status toggle or review mutation into non-volatile system storage logs.

### Admin User & Audit Log Schemas

```python
class AdminRole(str, enum.Enum):
    SUPER_ADMIN = "super_admin"
    COMPLIANCE_OFFICER = "compliance_officer"
    SUPPORT_AGENT = "support_agent"

class AdminUser(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    email: str = Field(unique=True, index=True)
    hashed_password: str
    totp_secret: str
    role: AdminRole
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)

class AuditLog(SQLModel, table=True):
    id: str = Field(default_factory=lambda: str(uuid4()), primary_key=True)
    admin_id: str = Field(foreign_key="adminuser.id", index=True)
    target_user_id: Optional[str] = Field(default=None, index=True)
    action: str = Field(..., description="e.g., KYC_APPROVE, ACCOUNT_LOCK")
    notes: str
    timestamp: datetime = Field(default_factory=datetime.utcnow)
```

---

## Summary Core Functional Endpoints Index

| Method & Path | Auth | Payload | Description |
| :--- | :--- | :--- | :--- |
| `POST /api/v1/auth/otp/request` | None | `{"phone_number": "str"}` | Fires verification PIN codes down the cellular pipe. |
| `POST /api/v1/auth/otp/verify` | None | `OTPVerifySchema` | Performs Redis lookups, generates active user JWT context parameters. |
| `GET /api/v1/feed/tasks` | JWT | None | Inner-joins across linked Doer skill attributes to serve specialized, localized task matrices. Includes delay filters and point-of-time startup coordinates. |
| `POST /api/v1/tasks` | JWT (Seeker) | Task Input Body | Creates a geolocated task item, capturing mandatory scheduling deadline_timestamp, dual waypoint parameters, and is_external_purchase flags. |
| `POST /api/v1/tasks/{id}/bids` | JWT (Doer) | Bid Input Body | Injects or alters service quote metrics if the target task state tracks strictly match OPEN. Accepts all-inclusive amounts. |
| `POST /api/v1/tasks/{id}/retract-bid` | JWT (Doer) | None | Allows a provider to safely withdraw an outstanding or active bid if field parameters change. |
| `POST /api/v1/tasks/{id}/accept-bid` | JWT (Seeker) | `{"bid_id": "str"}` | Locks transaction loops, shifting task records into review while preparing Paystack checkouts. Deducts estimated_item_budget prior to taxing labor commissions. |
| `POST /api/v1/payments/initialize` | JWT (Seeker) | `{"task_id": "str"}` | Computes explicit split parameters and compiles a verified checkout URL for secure frontend Paystack gateway WebView redirection. |
| `POST /api/v1/tasks/{id}/start` | JWT (Doer) | `{"start_pin": "str", "lat": float, "lng": float}` | Validates hardware-independent arrival matching criteria strictly against pickup_ boundaries to safely activate the job state. |
| `POST /api/v1/tasks/{id}/complete` | JWT (Doer) | `{"completion_pin": "str", "lat": float, "lng": float}` | Authenticates finalized task markers against destination_ coordinates for delivery tasks, releasing split-payment escrow payloads directly via registered provider payment accounts. |
| `POST /api/v1/tasks/{id}/dispute` | JWT | Dispute Ticket Body | Instantly shifts task state to DISPUTED, freezes escrow payloads, and flags the Jaspr operator queue. |
| `GET /api/v1/doer/reputation-score` | JWT (Doer) | None | Returns dynamic reputation score computed via database engine level SQL SUM operations. |
| `POST /api/v1/reviews/submit` | JWT | Review Input JSON | Commits a double-blind peer rating (1-5 stars) and comment string directly inside the immutable ReviewLedger. |
| `GET /api/v1/users/{id}/reviews` | JWT | None | Fetches chronological public text comments and star ratings to hydrate profile views within the Flutter view layers. |
| `GET /api/v1/seeker/reputation` | JWT (Seeker) | None | Returns the Seeker's calculated reputation total and platform access limits via active database aggregation. |
| `POST /api/v1/devices/token` | JWT | `{"fcm_token": "str", "device_os": "str"}` | Registers or overwrites device push identifier parameters upon mobile application boot lifecycle hooks. |
| `GET /api/v1/chat/{task_id}/history` | JWT | Query parameters | Presents paginated direct chat logs to accurately hydrate conversations when loading active interface screens. |
| `WS /api/v1/chat/ws` | Token Query Param | Duplex JSON Frames | Establishes persistent duplex WebSocket communication parameters for active foreground messaging. |
| `GET /api/v1/notifications/inbox` | JWT | Query parameters | Pulls a geolocated, paginated list of non-chat system alerts for the local user notification feed center. |
| `POST /api/v1/notifications/{id}/read` | JWT | None | Marks a specific historical system inbox alert entry as read to structurally clear platform badge counts. |
| `POST /api/v1/kyc/submit` | JWT | Multipart Form Data | Pipes user metrics and raw image files safely up to unindexed infrastructure. |
| `POST /api/v1/kyc/resume` | JWT | Multipart (PDF File) | Validates signatures, locks document stream directly within private S3 locations. |
| `POST /api/v1/callbacks/paystack` | Paystack Hash | Paystack Webhook Body | Validates SHA512 HMAC headers and processes transaction clearances to securely unlock provider execution tracks. |
| `POST /api/v1/callbacks/kyc-provider` | Provider Hash | Identity Provider JSON | Webhook callback route. Verifies provider cryptographic signatures and updates states directly. |
| `GET /api/v1/admin/kyc/review-queue` | Admin JWT | Query params | Presents real-time matching flags and unindexed presigned portfolio documents for admin inspection. |
| `POST /api/v1/admin/kyc/{user_id}/decision` | Admin JWT | `{"action": "str", "notes": "str"}` | Overrides or resolves edge validation profiles, recording tracking traces inside AuditLog. |
| `POST /api/v1/admin/disputes/{task_id}/resolve` | Admin JWT | Resolution Body | Executes administrative escrow overrides (FULL_REFUND, FULL_PAYOUT, PARTIAL_SPLIT) directly through Paystack. |
