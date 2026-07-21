from fastapi import APIRouter
from app.features.users.router.auth import router as auth_router
from app.features.users.router.otp import router as otp_router
from app.features.users.router.profile import router as profile_router
from app.features.users.router.kyc import router as kyc_router
from app.features.users.router.payouts import router as payouts_router

router = APIRouter()

router.include_router(auth_router)
router.include_router(otp_router)
router.include_router(profile_router)
router.include_router(kyc_router)
router.include_router(payouts_router)
