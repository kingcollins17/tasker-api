# Tasker App Architecture & Pricing Strategy

## Objective

Build an **on-demand dispatch and pricing engine** (like Bolt or Uber) that instantly matches customers with qualified, available providers, estimates task costs accurately, pays providers fairly, and generates platform revenue.

---

## Core Value Proposition

Shift from a manual bidding marketplace to an **instant-fulfillment dispatch engine**.

* **For Customers:** Zero hassle. No browsing profiles or comparing bids—just post a task and get a verified professional assigned automatically.
* **For Providers:** Zero unpaid time writing proposals—just stay online, receive fair payout offers, and accept jobs nearby.

---

## On-Demand Dispatch Flow

```text
[Customer Posts Task] 
        │
        ▼
[System Calculates Fixed Price & Scope]
        │
        ▼
[Matching Engine Finds Candidate Queue] ──(Filters: Radius + Category + Online + Rating)
        │
        ▼
[Dispatch Loop: Send Ping to Provider #1] 
        │
   ┌────┴────────────────────────┐
   │ Accept?                     │
   ├───────────────┬─────────────┤
   ▼ YES           ▼ NO / Timeout (30s)
[Task Assigned]  [Cascade to Provider #2]

```

### Flow Breakdown

1. **Customer Request:** Customer selects a category, fills required task details/photos, sees a locked-in price estimate, and taps **"Find Professional"**.
2. **Candidate Queue Generation:** System generates a ranked queue of eligible providers based on proximity, qualifications, and performance metrics.
3. **Cascading Dispatch:** The top candidate gets a 30-second ping with payout details and task distance. If ignored or declined, it instantly cascades to candidate #2.

---

## System Architecture

### 1. Key Services

* **Task Service:** Manages task state, scope, and lifecycle.
* **Pricing Engine:** Calculates upfront estimates based on base rates, modifiers, and surge.
* **Geolocation Engine:** Tracks provider heartbeats via Redis Geospatial/PostGIS and handles spatial queries (`ST_DWithin`).
* **Matchmaking Engine:** Builds and ranks provider queues per task request using a composite scoring function:

$$\text{Match Score} = w_1(\text{Distance}) + w_2(\text{Rating}) + w_3(\text{Completion Rate})$$

* **Cascading Dispatcher:** Background queue worker (e.g., Celery/Redis) managing 30-second timeout timers and assignment handoffs.
* **Promo & Fee Engine:** Handles discounts, platform commission splits, and payouts.

### 2. Data Model

#### `task_categories`

* `id`
* `name`
* `base_price`
* `default_duration_min`

#### `pricing_rules`

* `id`
* `category_id`
* `rule_type`
* `value`
* `multiplier`

#### `provider_locations`

* `provider_id`
* `lat`
* `lng`
* `is_online`
* `last_heartbeat`

#### `tasks`

* `id`
* `category_id`
* `customer_id`
* `assigned_provider_id`
* `status` *(e.g., SEARCHING, ASSIGNED, IN_PROGRESS, COMPLETED)*
* `estimated_price`
* `final_price`
* `platform_fee`
* `provider_payout`

---

## Pricing Engine & Revenue Model

### 1. Base Pricing Formula

$$\text{Price} = \text{Base Price} + \text{Distance Fee} + \text{Time Fee} + \text{Urgency Fee} + \text{Complexity Fee}$$

### 2. Suggested Category Base Prices

| Category | Suggested Base |
| --- | --- |
| **Delivery / Errand** | ₦2,000 |
| **Cleaning** | ₦5,000 |
| **Plumbing** | ₦8,000 |
| **Electrical Repair** | ₦10,000 |

### 3. Modifiers & Dynamic Pricing

* **Distance Fee:** Charge for provider travel (e.g., ₦150/km)
* **Time Fee:** Charge for expected duration (e.g., ₦1,200/hour)
* **Urgency Fee:** Same-day / instant requests (+₦1,000)
* **Dynamic Surge:** Multiplier applied based on localized demand ratio:

$$\text{Demand Ratio} = \frac{\text{Open Tasks in Zone}}{\text{Available Providers in Zone}}$$

| Demand Ratio | Multiplier |
| --- | --- |
| **< 1.0** | 1.0× |
| **1.0 – 2.0** | 1.1× |
| **2.0 – 3.0** | 1.25× |
| **> 3.0** | 1.5× |

### 4. Revenue & Commission Split

```text
subtotal = basePrice + distanceFee + timeFee + urgencyFee + complexityFee
customerPrice = subtotal * demandMultiplier
platformFee = customerPrice * 0.15
providerPayout = customerPrice - platformFee

```

> **Default Platform Commission:** 15%

---

## Practical Example

> **Task:** Furniture Assembly

| Item | Amount |
| --- | --- |
| Base Price | ₦6,000 |
| Distance (8 km) | ₦1,200 |
| Time (2 hr) | ₦3,000 |
| Urgent Request | ₦1,000 |
| **Customer Price** | **₦11,200** |
| **Provider Payout (85%)** | **₦9,520** |
| **Platform Fee (15%)** | **₦1,680** |

---

## Key Tuning Metric: Task Acceptance Rate

$$\text{Acceptance Rate} = \frac{\text{Tasks Accepted Within 5 Minutes}}{\text{Total Tasks Posted}}$$

* **Low Acceptance Rate (< 50%):** Task pricing is too low or distance fees aren't compensating providers enough.
* **Instant Acceptance Rate (~100%):** Task pricing may be higher than necessary.
* **Target Healthy Range:** **60% – 80%**

---

## Edge Case Handling Strategies

1. **Scope Creep / On-site Adjustments:** Standardize in-app extra items (e.g., additional materials, unexpected labor). Require customer in-app approval before extra work begins.
2. **Timeout Safeguard:** If no provider accepts within 3 minutes of cascading, prompt the customer to increase the urgency tip/surge or widen the search radius.
3. **Provider Decline Penalties:** Automatically pause or lower priority for providers who repeatedly ignore or reject incoming dispatch pings.