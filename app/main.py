import multiprocessing
import sys
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.database import init_db
from app.core.services import get_cache_service
from app.features.users.router import router as users_router
from app.features.regions.router import router as regions_router
from app.features.services.routers import router as services_router
# Global variables to hold the celery processes references
celery_process = None
celery_beat_process = None


def run_celery_worker():
    from app.celery_app import celery_app
    args = ["worker", "--loglevel=info"]
    if sys.platform == "win32":
        args.append("--pool=solo")
    celery_app.worker_main(args)


def run_celery_beat():
    from app.celery_app import celery_app
    celery_app.start(["beat", "--loglevel=info"])


@asynccontextmanager
async def lifespan(app: FastAPI):
    global celery_process, celery_beat_process
    # Startup logic
    print(f"Starting up {settings.PROJECT_NAME}...")
    await init_db()

    # Start Celery worker and beat processes using multiprocessing
    # try:
    #     multiprocessing.set_start_method("spawn", force=True)
    # except RuntimeError:
    #     pass

    # celery_process = multiprocessing.Process(target=run_celery_worker, daemon=True)
    # celery_process.start()
    # print("Celery worker process started.")

    # celery_beat_process = multiprocessing.Process(target=run_celery_beat, daemon=True)
    # celery_beat_process.start()
    # print("Celery beat process started.")

    yield
    # Shutdown logic
    # print(f"Shutting down {settings.PROJECT_NAME}...")
    # if celery_process and celery_process.is_alive():
    #     print("Terminating Celery worker process...")
    #     celery_process.terminate()
    #     celery_process.join(timeout=5)
    #     if celery_process.is_alive():
    #         celery_process.kill()

    # if celery_beat_process and celery_beat_process.is_alive():
    #     print("Terminating Celery beat process...")
    #     celery_beat_process.terminate()
    #     celery_beat_process.join(timeout=5)
    #     if celery_beat_process.is_alive():
    #         celery_beat_process.kill()

    await get_cache_service().close()


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.PROJECT_NAME,
        description="Tasker API with FastAPI, SQLModel, and Celery",
        version=settings.VERSION,
        lifespan=lifespan,
    )

    # CORS Middleware Setup
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # Adjust in production
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Global Exception Handlers
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        return JSONResponse(
            status_code=500,
            content={"message": "An unexpected error occurred.",
                     "details": str(exc)},
        )

    # Include API Routers
    API_V1_PREFIX = '/api/v1'
    app.include_router(
        users_router, prefix=f"{API_V1_PREFIX}/users", tags=["Users"])
    app.include_router(
        regions_router, prefix=f"{API_V1_PREFIX}/regions", tags=["Regions"])
    app.include_router(
        services_router, prefix=f"{API_V1_PREFIX}")

    @app.get("/")
    async def read_root():
        return {
            "name": settings.PROJECT_NAME,
            "version": settings.VERSION,
            "status": "healthy",
            "documentation": "/docs"
        }

    @app.get("/health")
    async def health_check():
        return {"status": "ok"}

    return app


app = create_app()
