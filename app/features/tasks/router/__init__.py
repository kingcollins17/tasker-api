from fastapi import APIRouter
from app.features.tasks.router.tasks import router as tasks_router
from app.features.tasks.router.assignments import router as assignments_router
from app.features.tasks.router.admin_tasks import router as admin_tasks_router

router = APIRouter()

router.include_router(tasks_router)
router.include_router(assignments_router)
router.include_router(admin_tasks_router)
