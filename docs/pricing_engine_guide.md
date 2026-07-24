
# Dynamic Pricing Engine Guide

## Overview

The **PricingEngine** ([app/features/services/pricing_engine.py](file:///Users/mac/collins/dev/tasker-api/app/features/services/pricing_engine.py)) provides automated, locked upfront price calculations for task requests in the on-demand dispatch marketplace.

It evaluates database-backed dynamic rules ([pricing_rules](file:///Users/mac/collins/dev/tasker-api/app/core/models/services.py#L76-L92)), service definitions, geographical distance, labor estimates, urgency surcharges, localized demand surge ratios, and platform commission take rates.

---

## 1. Upfront Pricing Formula

$$\text{Subtotal} = \text{base\_price} + \text{distance\_fee} + \text{time\_fee} + \text{urgency\_fee} + \text{complexity\_fee}$$

$$\text{Customer Price} = \text{Subtotal} \times \text{surge\_multiplier}$$

$$\text{Platform Fee} = \text{Customer Price} \times \text{take\_rate} \quad (\text{Default: } 15\%)$$

$$\text{Provider Payout} = \text{Customer Price} - \text{Platform Fee}$$

---

## 2. Parameter Calculation Breakdown

| Parameter | Source & Evaluation Logic |
| :--- | :--- |
| **`base_price`** | Resolved from `PricingRule(rule_type='base_rate')` > `Service.base_price` > `ServiceCategory.default_base_price`. |
| **`distance_fee`** | $\text{distance\_km} \times \text{per\_km\_rate}$. Rate resolved from `PricingRule(rule_type='per_km')` > `Service.per_km_rate` > `ServiceCategory.per_km_rate` (default ₦150/km). |
| **`time_fee`** | $\text{duration\_min} \times \text{per\_minute\_rate}$. Rate resolved from `PricingRule(rule_type='per_minute')` > `Service.per_minute_rate` > `ServiceCategory.per_minute_rate` (default ₦20/min). |
| **`urgency_fee`** | Evaluated if `is_urgent=True`. Value resolved from `PricingRule(rule_type='urgency_fee')` or fallback default of ₦1,000. |
| **`complexity_fee`** | Flat fee additive for specialized equipment or high-difficulty labor resolved from `PricingRule(rule_type='complexity_flat')` + request complexity input. |
| **`surge_multiplier`** | Scaling factor calculated from demand-to-supply ratio or `PricingRule(rule_type='surge_multiplier')` override. |

---

## 3. Dynamic Surge Multiplier Logic

$$\text{Demand Ratio} = \frac{\text{Open Tasks in Region Zone}}{\text{Available Providers in Region Zone}}$$

| Demand Ratio ($\mathbf{R}$) | Multiplier | Description |
| :--- | :--- | :--- |
| **$R < 1.0$** | **1.0×** | Normal supply & demand balance. |
| **$1.0 \le R < 2.0$** | **1.1×** | Slight demand increase (+10%). |
| **$2.0 \le R < 3.0$** | **1.25×** | High localized demand (+25%). |
| **$R \ge 3.0$** | **1.5×** | Severe provider deficit / surge (+50%). |

---

## 4. Rule Resolution Hierarchy

When executing price calculations, `PricingEngine` queries active `pricing_rules` matching:
1. `category_id` (or fallback to global category rule if null)
2. `region_id` (or fallback to global region rule if null)

Rules are applied in order of specificity to override service/category default rates.

---

## 5. Usage Example

```python
from app.core.repository import Repository
from app.core.models.services import PricingRule, ServiceCategory, Service
from app.features.services.pricing_engine import PricingEngine, PricingCalculationRequest

# Initialize PricingEngine with repositories
pricing_engine = PricingEngine(
    pricing_rule_repo=Repository(PricingRule, session),
    category_repo=Repository(ServiceCategory, session),
    service_repo=Repository(Service, session),
)

# Calculate upfront price for furniture assembly task
request = PricingCalculationRequest(
    category_id="cat-uuid-plumbing",
    service_id="srv-uuid-leak-repair",
    region_id="reg-uuid-lagos",
    distance_km=8.0,
    estimated_duration_min=120,
    is_urgent=True,
    open_tasks_in_zone=15,
    available_providers_in_zone=5,  # Demand Ratio = 3.0 -> 1.5x surge
)

breakdown = await pricing_engine.calculate_price(request)

# Output Breakdown
print(breakdown.customer_total_price)  # Total locked price charged to customer
print(breakdown.platform_fee)          # 15% platform commission
print(breakdown.provider_payout)       # Net earnings paid out to provider

# Lock pricing onto Task SQLModel entity
pricing_engine.apply_pricing_to_task(task_entity, breakdown)
```
