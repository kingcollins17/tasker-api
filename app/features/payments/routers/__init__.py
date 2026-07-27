from fastapi import APIRouter
from .payments import router as payments_router
from .stats import router as stats_router

router = APIRouter()
router.include_router(payments_router)
router.include_router(stats_router)
