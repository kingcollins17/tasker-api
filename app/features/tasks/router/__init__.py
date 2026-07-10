from fastapi import APIRouter
from app.features.tasks.router.tasks import router as tasks_router
from app.features.tasks.router.bids import router as bids_router

router = APIRouter()

router.include_router(tasks_router)
router.include_router(bids_router)
