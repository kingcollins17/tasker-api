from fastapi import APIRouter
from app.features.vetting.router.vetting import router as vetting_router
from app.features.vetting.router.admin import router as admin_vetting_router

router = APIRouter()
router.include_router(vetting_router)
router.include_router(admin_vetting_router)
