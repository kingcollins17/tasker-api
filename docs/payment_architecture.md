# Task Payment Architecture

## Overview

Tasker supports **two payment modes**: Online card payment (via Paystack link) and In-Person Cash payment to the provider. 

**NO payment is triggered when a task is posted.** Payment processing is strictly triggered upon **task completion** when the provider submits the **4-digit completion PIN**.

---

## Task Completion & Payment Trigger Flow

When the provider enters the 4-digit PIN to complete a task, they specify the `payment_mode` (`cash` or `online`):

```
Provider enters 4-digit completion PIN
  │
  ├── Selects payment_mode: "cash"
  │     ├── Provider received cash directly from customer on-site
  │     ├── System calculates platform fee for that task
  │     ├── System records debt entry in provider_debts table (status=PENDING)
  │     └── Task marked COMPLETED (cash settled on-site)
  │
  └── Selects payment_mode: "online"
        ├── Money passes through platform (we deduct platform fee)
        ├── System initializes Paystack payment checkout link
        ├── System sends payment notification to Customer via IN_APP, PUSH, and EMAIL
        └── Customer clicks link and completes payment:
              └── Paystack Webhook fires:
                    ├── Inserts Transaction record (type=TASK_PAYMENT, status=SUCCESS)
                    ├── Marks task payment_status = PAID
                    └── Immediately enqueues Celery task process_provider_payout:
                          ├── Offsets any pending provider debts from payout
                          ├── Transfers net payout out via Paystack transfer API
                          └── Inserts Transaction record (type=PROVIDER_PAYOUT)
```

---

## Detailed Payment Modes

### 1. In-Person Cash Payment (`payment_mode = "cash"`)

- Provider receives cash directly from customer on-site.
- On entering the completion PIN, provider selects `payment_mode = "cash"`.
- The platform calculates the commission fee (`platform_fee`).
- The system appends a positive debt entry (`+platform_fee`, `reason=cash_task_commission`) in `provider_debts` ledger.
- Net debt balance is computed dynamically as `SUM(amount) WHERE provider_id = :id`.
- Task status is updated to `COMPLETED` and `payment_status = "cash_paid"`.

### 2. Online Payment (`payment_mode = "online"`)

- On entering the completion PIN, provider selects `payment_mode = "online"`.
- System calculates `customer_total_price`, `platform_fee`, and `provider_payout`.
- System initializes Paystack checkout session to generate a payment link.
- System sends notification containing the checkout link to the Customer across **3 channels**:
  1. **In-App Notification**
  2. **Push Notification**
  3. **Email Notification**
- Customer opens link and pays on Paystack.
- **Paystack Webhook Listener**:
  - Validates HMAC signature.
  - Inserts `Transaction(type=TASK_PAYMENT, amount=customer_total_price, status=SUCCESS)`.
  - Updates `task.payment_status = "paid"`.
  - Enqueues Celery task to transfer `provider_payout` to the provider's linked payment account.

---

## Data Model Updates

### `Task` Model
- `payment_mode`: Optional Enum (`"cash"`, `"online"`)
- `payment_status`: Enum (`"pending"`, `"payment_requested"`, `"paid"`, `"cash_paid"`, `"failed"`)
- `payment_url`: Optional string (Paystack checkout URL generated for online payments)

### `Transaction` Model
- `transaction_type`:
  - `TASK_PAYMENT`: Customer payment via gateway
  - `PROVIDER_PAYOUT`: Transfer of net earnings to provider
  - `CASH_COMMISSION_DEBT`: Debt owed by provider for platform fee on cash tasks
  - `REFUND`: Customer refund
- `payment_mode`: Optional string (`"cash"`, `"online"`)

---

## Notification Pipeline for Online Payment Request

When `payment_mode = "online"` is selected during PIN completion:

```python
CreateNotification(
    type=NotificationType.PAYMENT_REQUESTED,  # or TASK_COMPLETED_PAYMENT_DUE
    title="Payment Requested for Task",
    body=f"Your task '{task.title}' is completed. Tap to pay ₦{task.customer_total_price:,.2f}.",
    recipient_ids=[task.customer_id],
    channels=["in_app", "push", "email"],
    data={
        "task_id": task.id,
        "payment_url": checkout_url,
        "amount": task.customer_total_price,
    }
)
```

---

## Celery Tasks Architecture

| Task Name | Queue | Trigger | Description |
|---|---|---|---|
| `payments.request_online_payment` | `payments` | PIN completion (`online` mode) | Initializes Paystack link & sends in-app, push, email notifications |
| `payments.record_cash_commission_debt` | `payments` | PIN completion (`cash` mode) | Records platform fee debt transaction for provider |
| `payments.process_provider_payout` | `payments` | Paystack webhook (`charge.success`) | Transfers `provider_payout` to provider's bank account |
