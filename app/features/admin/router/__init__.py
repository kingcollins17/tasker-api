from fastapi import APIRouter
from app.features.admin.router.auth import router as auth_router
from app.features.admin.router.management import router as management_router
from app.features.admin.router.audit import router as audit_router

router = APIRouter()

router.include_router(auth_router)
router.include_router(management_router)
router.include_router(audit_router)
