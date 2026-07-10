from fastapi import APIRouter

from app.features.notifications.router.notifications import router as notifications_router
from app.features.notifications.router.preferences import router as preferences_router

router = APIRouter()

router.include_router(notifications_router)
router.include_router(preferences_router, prefix="/preferences", tags=["Notification Preferences"])
