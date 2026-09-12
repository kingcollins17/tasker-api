# Taska Support & Dispute System

## Goal

Build a scalable case management system for Taska where customers and providers can create support tickets/disputes, support agents can manage them, communicate through email, and users can see the complete case timeline.

Design it so in-app messaging can be added later without changing the core architecture.

## Architecture

Use one unified `SupportCase` entity for both support tickets and disputes.

```text
SupportCase
├── CaseMessage
├── CaseEvent
├── CaseAssignment
├── CaseAttachment
├── Dispute (optional)
└── CaseResolution (optional)
```

PostgreSQL is the source of truth. Celery + Redis handles asynchronous email/notification work.

---

## Core Models

### SupportCase

```text
id
case_number              # e.g. SUP-100023
type                     # GENERAL, DISPUTE, PAYMENT, TASK_ISSUE, etc.
status                   # OPEN, IN_PROGRESS, WAITING_FOR_USER,
                         # WAITING_FOR_INTERNAL, RESOLVED, CLOSED
priority                 # LOW, NORMAL, HIGH, URGENT

customer_id              nullable
provider_id              nullable
task_id                  nullable
booking_id               nullable
payment_id               nullable

subject
description

assigned_agent_id        nullable

first_response_due_at
resolution_due_at
first_responded_at
resolved_at
closed_at

created_at
updated_at
```

Add indexes on:

```text
status
priority
assigned_agent_id
customer_id
provider_id
task_id
created_at
updated_at
```

### Dispute

Only created when `SupportCase.type == DISPUTE`.

```text
id
case_id
task_id
booking_id

initiated_by
reason

amount_disputed
currency

requested_resolution

created_at
```

### CaseMessage

Represents actual communication.

```text
id
case_id

sender_type             # CUSTOMER, PROVIDER, AGENT, SYSTEM
sender_id

channel                 # EMAIL, IN_APP
visibility              # PUBLIC, INTERNAL

body

email_message_id        nullable
created_at
```

Keep internal agent notes as messages with:

```text
visibility = INTERNAL
```

Users must only receive `PUBLIC` messages.

### CaseEvent

Append-only audit/timeline history.

```text
id
case_id

event_type
actor_type
actor_id

metadata JSONB

created_at
```

Examples:

```text
CASE_CREATED
CASE_ASSIGNED
CASE_REASSIGNED
STATUS_CHANGED
PRIORITY_CHANGED
MESSAGE_SENT
MESSAGE_RECEIVED
INTERNAL_NOTE_ADDED
ATTACHMENT_ADDED
DISPUTE_OPENED
DISPUTE_ESCALATED
CASE_RESOLVED
CASE_REOPENED
CASE_CLOSED
```

Never edit or delete events.

### CaseAssignment

Preserve assignment history.

```text
id
case_id
agent_id
assigned_by
assigned_at
unassigned_at
reason
```

`SupportCase.assigned_agent_id` stores the current assignment for fast queries.

### CaseAttachment

Do not store files in PostgreSQL.

```text
id
case_id
message_id

uploaded_by
storage_key

filename
mime_type
size

created_at
```

Store actual files in object storage.

### CaseResolution

```text
id
case_id

decision
reason
resolved_by

created_at
```

Financial actions such as refunds must use the existing/payment financial system. Do not treat a dispute field as proof that money was refunded.

---

## Case Lifecycle

```text
OPEN
  ↓
IN_PROGRESS
  ↓
WAITING_FOR_CUSTOMER / WAITING_FOR_PROVIDER
  ↓
IN_PROGRESS
  ↓
RESOLVED
  ↓
CLOSED
```

Allow `RESOLVED -> OPEN` when a user reopens a case.

Every state change must create a `CaseEvent`.

---

## API

### Customer / Provider

```http
POST /api/v1/support/cases
GET  /api/v1/support/cases
GET  /api/v1/support/cases/{case_id}
GET  /api/v1/support/cases/{case_id}/messages
GET  /api/v1/support/cases/{case_id}/timeline

POST /api/v1/support/cases/{case_id}/messages
POST /api/v1/support/cases/{case_id}/attachments

POST /api/v1/support/cases/{case_id}/close
POST /api/v1/support/cases/{case_id}/reopen
```

### Disputes

```http
POST /api/v1/disputes
GET  /api/v1/disputes/{case_id}
```

### Support Agents

```http
GET   /api/v1/admin/support/cases
GET   /api/v1/admin/support/cases/{case_id}

POST  /api/v1/admin/support/cases/{case_id}/assign
POST  /api/v1/admin/support/cases/{case_id}/messages
POST  /api/v1/admin/support/cases/{case_id}/notes

PATCH /api/v1/admin/support/cases/{case_id}

POST  /api/v1/admin/support/cases/{case_id}/resolve
POST  /api/v1/admin/support/cases/{case_id}/escalate
```

Apply strict authorization so customers/providers can only access their own cases.

---

## Email Architecture

Email is a communication channel, not the source of truth.

Agent sends message:

```text
API
 ↓
Create CaseMessage
 ↓
Create CaseEvent
 ↓
Commit transaction
 ↓
Queue Celery email task
 ↓
Email provider
```

Never make the API request wait for email delivery.

Support outgoing emails must contain a unique case/reply token so incoming email can be associated with the correct case.

```text
Customer replies to email
        ↓
Email provider webhook
        ↓
FastAPI
        ↓
Resolve case using reply token
        ↓
Create CaseMessage
        ↓
Create CaseEvent
        ↓
Notify assigned agent
```

Store the provider's email/message ID to support idempotency and prevent duplicate messages.

---

## Service Layer

Do not put support business logic directly inside route handlers.

Create:

```text
support/
├── models/
├── schemas/
├── services/
│   ├── case_service.py
│   ├── dispute_service.py
│   ├── message_service.py
│   ├── assignment_service.py
│   └── resolution_service.py
├── repositories/
├── api/
│   ├── customer.py
│   ├── provider.py
│   └── agent.py
└── tasks/
    ├── email.py
    └── notifications.py
```

Services must handle transactional operations such as:

```text
update case
create event
create message/resolution
update timestamps
commit transaction
queue background work
```

---

## Timeline

The timeline endpoint should combine `CaseEvent` records and relevant public messages into chronological order.

Example:

```text
08:30  Customer opened dispute
08:31  Assigned to Agent John
08:35  Agent responded
09:02  Customer replied
09:15  Agent requested evidence
10:30  Customer uploaded evidence
11:00  Case resolved
```

Do not reconstruct historical state from the current `SupportCase`.

---

## Scalability Rules

Keep PostgreSQL as the source of truth.

Use indexes for agent dashboard queries.

Use pagination for cases, messages, and timeline.

Use cursor/keyset pagination for large message/timeline datasets rather than loading everything.

Use Celery for:

```text
email sending
email notifications
user notifications
SLA monitoring
other slow/background operations
```

Use idempotency checks for incoming email/webhooks.

Do not introduce Kafka, Elasticsearch, CQRS, or microservices at this stage.

The system should remain a normal FastAPI + PostgreSQL + Celery/Redis architecture.

---

## Future In-App Chat

Do not redesign the database when chat is introduced.

`CaseMessage.channel` already supports:

```text
EMAIL
IN_APP
```

Adding in-app chat should only require:

```text
WebSocket/HTTP messaging
notification delivery
read/unread state
```

The same `SupportCase` and `CaseMessage` models remain the source of truth.

## Implementation Priority

Build in this order:

```text
SupportCase + enums
        ↓
CaseEvent
        ↓
CaseMessage
        ↓
Dispute
        ↓
Assignment/history
        ↓
Attachments
        ↓
Agent APIs
        ↓
Customer/provider APIs
        ↓
Email sending via Celery
        ↓
Incoming email webhook
        ↓
Resolution/dispute workflow
        ↓
SLA monitoring
```

Keep all support/dispute state changes inside service-layer transactions and generate an audit event for every meaningful action.
