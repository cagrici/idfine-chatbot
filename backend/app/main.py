import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.router import api_router
from app.api.websocket import router as ws_router
from app.config import get_settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version="1.0.0",
        docs_url="/api/docs" if settings.debug else None,
        redoc_url="/api/redoc" if settings.debug else None,
    )

    # CORS
    if settings.debug:
        origins = ["*"]
    else:
        origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=settings.debug is False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Static file serving for uploads
    import os
    upload_dir = settings.upload_dir
    os.makedirs(upload_dir, exist_ok=True)
    app.mount("/uploads", StaticFiles(directory=upload_dir), name="uploads")

    # Routers
    app.include_router(api_router)
    app.include_router(ws_router)

    @app.get("/health")
    async def health_check():
        return {"status": "ok", "service": settings.app_name}

    @app.on_event("startup")
    async def startup():
        logger.info("Starting %s", settings.app_name)

        # Create database tables if they don't exist
        from app.db.database import Base, engine
        from app.models import activity_log, audit, conversation, document, product, role, user, widget_config  # noqa: F401 - import for table registration
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        logger.info("Database tables ensured")

        # Preload embedding model to avoid first-request latency
        try:
            from app.services.embedding_service import get_embedding_model
            get_embedding_model()
            logger.info("Embedding model preloaded successfully")
        except Exception as e:
            logger.warning("Failed to preload embedding model: %s", e)

    return app


app = create_app()
