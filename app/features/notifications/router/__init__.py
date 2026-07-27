from fastapi import APIRouter

from app.features.notifications.router.notifications import router as notifications_router

router = APIRouter()

router.include_router(notifications_router)
