from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup logic
    print(f"Starting up {settings.PROJECT_NAME}...")
    yield
    # Shutdown logic
    print(f"Shutting down {settings.PROJECT_NAME}...")

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
            content={"message": "An unexpected error occurred.", "details": str(exc)},
        )

    # Include API Routers (when implemented)
    # from app.features.auth.router import router as auth_router
    # app.include_router(auth_router, prefix="/api/v1/auth", tags=["auth"])

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
