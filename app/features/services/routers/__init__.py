from fastapi import APIRouter
from .categories import router as categories_router
from .services import router as services_router

router = APIRouter()
router.include_router(categories_router)
router.include_router(services_router)
