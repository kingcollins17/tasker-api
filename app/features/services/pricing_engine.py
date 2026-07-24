import math
from typing import List, Optional
from fastapi import Depends
from pydantic import BaseModel, Field
from sqlmodel import select

from app.core.models.services import (
    PricingRule,
    PricingRuleType,
    Service,
    ServiceCategory,
)
from app.core.models.tasks import Task
from app.core.repository import GetRepository, Repository


class PricingCalculationRequest(BaseModel):
    """Input parameters provided for calculating a task's upfront price."""
    category_id: Optional[str] = Field(default=None, description="Target service category ID")
    service_id: Optional[str] = Field(default=None, description="Target specific service ID")
    region_id: Optional[str] = Field(default=None, description="Geographical region ID for location-based pricing rules")
    distance_km: Optional[float] = Field(default=0.0, description="Estimated provider travel distance in kilometers")
    estimated_duration_min: Optional[int] = Field(default=None, description="Estimated task execution duration in minutes")
    is_urgent: Optional[bool] = Field(default=False, description="Whether immediate or same-day dispatch is requested")
    complexity_fee: Optional[float] = Field(default=0.0, description="Flat complexity fee for special tools or high labor requirements")
    open_tasks_in_zone: Optional[int] = Field(default=None, description="Active unassigned open tasks in region for surge calculation")
    available_providers_in_zone: Optional[int] = Field(default=None, description="Online available providers in region for surge calculation")
    surge_multiplier_override: Optional[float] = Field(default=None, description="Manual surge multiplier override")


class PricingBreakdown(BaseModel):
    """Calculated cost breakdown breakdown returned by the pricing engine."""
    base_price: Optional[float] = Field(default=0.0, description="Base task entry price")
    distance_fee: Optional[float] = Field(default=0.0, description="Compue ted travel distance fee")
    time_fee: Optional[float] = Field(default=0.0, description="Computed labor time fee")
    urgency_fee: Optional[float] = Field(default=0.0, description="Computed urgency surcharge fee")
    complexity_fee: Optional[float] = Field(default=0.0, description="Applied complexity fee")
    surge_multiplier: Optional[float] = Field(default=1.0, description="Dynamic demand surge scaling multiplier")
    subtotal: Optional[float] = Field(default=0.0, description="Subtotal before surge multiplier is applied")
    customer_total_price: Optional[float] = Field(default=0.0, description="Total locked-in customer price")
    platform_fee: Optional[float] = Field(default=0.0, description="Platform commission fee deducted")
    provider_payout: Optional[float] = Field(default=0.0, description="Net earnings payout delivered to provider")
    take_rate: Optional[float] = Field(default=0.15, description="Platform commission percentage applied")


class PricingEngine:
    """Calculates locked upfront task prices, distance fees, time fees, urgency surcharges, surge multipliers, and payout splits."""

    def __init__(
        self,
        pricing_rule_repo: Repository[PricingRule],
        category_repo: Repository[ServiceCategory],
        service_repo: Repository[Service],
    ):
        self.pricing_rule_repo = pricing_rule_repo
        self.category_repo = category_repo
        self.service_repo = service_repo

    async def calculate_price(self, request: PricingCalculationRequest) -> PricingBreakdown:
        """Evaluates pricing rules, categories, and services to compute an upfront price breakdown."""
        # 1. Fetch Service and ServiceCategory defaults
        target_service: Optional[Service] = None
        target_category: Optional[ServiceCategory] = None

        if request.service_id:
            target_service = await self.service_repo.get(request.service_id)
            if target_service and target_service.category_id:
                target_category = await self.category_repo.get(target_service.category_id)

        if not target_category and request.category_id:
            target_category = await self.category_repo.get(request.category_id)

        # Base fallbacks from model definitions
        base_price: float = 0.0
        default_duration_min: int = 60
        per_km_rate: float = 150.0
        per_minute_rate: float = 20.0
        take_rate: float = 0.15

        if target_category:
            base_price = target_category.default_base_price or 0.0
            default_duration_min = target_category.default_duration_min or 60
            per_km_rate = target_category.per_km_rate or 150.0
            per_minute_rate = target_category.per_minute_rate or 20.0

        if target_service:
            if target_service.base_price and target_service.base_price > 0:
                base_price = target_service.base_price
            if target_service.default_duration_min:
                default_duration_min = target_service.default_duration_min
            if target_service.per_km_rate:
                per_km_rate = target_service.per_km_rate
            if target_service.per_minute_rate:
                per_minute_rate = target_service.per_minute_rate
            if target_service.take_rate is not None:
                take_rate = target_service.take_rate

        # 2. Fetch and apply active PricingRule overrides from database
        stmt = select(PricingRule).where(PricingRule.is_active == True) # noqa: E712
        if request.category_id or (target_category and target_category.id):
            cat_id = request.category_id or (target_category.id if target_category else None)
            stmt = stmt.where((PricingRule.category_id == cat_id) | (PricingRule.category_id == None)) # noqa: E711
        if request.region_id:
            stmt = stmt.where((PricingRule.region_id == request.region_id) | (PricingRule.region_id == None)) # noqa: E711

        result = await self.pricing_rule_repo.execute(stmt)
        active_rules: List[PricingRule] = list(result.all())

        urgency_fee: float = 0.0
        complexity_fee: float = request.complexity_fee or 0.0
        surge_multiplier: float = 1.0

        for rule in active_rules:
            if rule.rule_type == PricingRuleType.BASE_RATE and rule.value and rule.value > 0:
                base_price = rule.value
            elif rule.rule_type == PricingRuleType.PER_KM and rule.value and rule.value > 0:
                per_km_rate = rule.value
            elif rule.rule_type == PricingRuleType.PER_MINUTE and rule.value and rule.value > 0:
                per_minute_rate = rule.value
            elif rule.rule_type == PricingRuleType.URGENCY_FEE and rule.value:
                if request.is_urgent:
                    urgency_fee = rule.value
            elif rule.rule_type == PricingRuleType.COMPLEXITY_FLAT and rule.value:
                complexity_fee += rule.value
            elif rule.rule_type == PricingRuleType.SURGE_MULTIPLIER and rule.multiplier:
                surge_multiplier = max(surge_multiplier, rule.multiplier)

        # Fallback urgency fee if urgent and no specific rule existed
        if request.is_urgent and urgency_fee == 0.0:
            urgency_fee = 1000.0

        # 3. Calculate distance and time fees
        distance_km = max(0.0, request.distance_km or 0.0)
        distance_fee = round(distance_km * per_km_rate, 2)

        duration_min = request.estimated_duration_min or default_duration_min
        time_fee = round(duration_min * per_minute_rate, 2)

        # 4. Calculate dynamic surge multiplier from demand ratio if provided
        if request.surge_multiplier_override and request.surge_multiplier_override > 0:
            surge_multiplier = request.surge_multiplier_override
        elif request.open_tasks_in_zone is not None and request.available_providers_in_zone is not None:
            avail = max(1, request.available_providers_in_zone)
            demand_ratio = request.open_tasks_in_zone / avail
            if demand_ratio >= 3.0:
                surge_multiplier = max(surge_multiplier, 1.5)
            elif demand_ratio >= 2.0:
                surge_multiplier = max(surge_multiplier, 1.25)
            elif demand_ratio >= 1.0:
                surge_multiplier = max(surge_multiplier, 1.1)

        # 5. Compute subtotal and final prices
        subtotal = round(base_price + distance_fee + time_fee + urgency_fee + complexity_fee, 2)
        customer_total_price = round(subtotal * surge_multiplier, 2)

        platform_fee = round(customer_total_price * take_rate, 2)
        provider_payout = round(customer_total_price - platform_fee, 2)

        return PricingBreakdown(
            base_price=round(base_price, 2),
            distance_fee=distance_fee,
            time_fee=time_fee,
            urgency_fee=round(urgency_fee, 2),
            complexity_fee=round(complexity_fee, 2),
            surge_multiplier=round(surge_multiplier, 2),
            subtotal=subtotal,
            customer_total_price=customer_total_price,
            platform_fee=platform_fee,
            provider_payout=provider_payout,
            take_rate=take_rate,
        )

    def apply_pricing_to_task(self, task: Task, breakdown: PricingBreakdown) -> Task:
        """Helper to assign calculated pricing breakdown fields onto a Task SQLModel entity."""
        task.base_price = breakdown.base_price
        task.distance_fee = breakdown.distance_fee
        task.time_fee = breakdown.time_fee
        task.urgency_fee = breakdown.urgency_fee
        task.complexity_fee = breakdown.complexity_fee
        task.surge_multiplier = breakdown.surge_multiplier
        task.customer_total_price = breakdown.customer_total_price
        task.platform_fee = breakdown.platform_fee
        task.provider_payout = breakdown.provider_payout
        return task


def get_pricing_engine(
    pricing_rule_repo: Repository[PricingRule] = Depends(GetRepository(PricingRule)),
    category_repo: Repository[ServiceCategory] = Depends(GetRepository(ServiceCategory)),
    service_repo: Repository[Service] = Depends(GetRepository(Service)),
) -> PricingEngine:
    return PricingEngine(
        pricing_rule_repo=pricing_rule_repo,
        category_repo=category_repo,
        service_repo=service_repo,
    )

