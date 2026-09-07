from fastapi import APIRouter
from .reviews import router as reviews_router

router = APIRouter()
router.include_router(reviews_router)
