from fastapi import APIRouter
from app.features.support.router.customer import router as customer_router
from app.features.support.router.dispute import router as dispute_router
from app.features.support.router.agent import router as agent_router
from app.features.support.router.webhook import router as webhook_router

router = APIRouter()

router.include_router(customer_router, prefix="/support", tags=["Support"])
router.include_router(dispute_router, prefix="/disputes", tags=["Disputes"])
router.include_router(agent_router, prefix="/admin/support/cases", tags=["Admin Support"])
router.include_router(webhook_router, prefix="/support", tags=["Support Webhooks"])
