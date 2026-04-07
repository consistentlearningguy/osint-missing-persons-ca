"""FastAPI app factory for optional backend mode."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.api.cases import router as cases_router
from backend.api.exports import router as exports_router
from backend.api.investigations import router as investigations_router
from backend.api.sync import router as sync_router
from backend.core.database import init_db
from backend.core.scheduler import start_scheduler, stop_scheduler


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    start_scheduler()
    yield
    stop_scheduler()


def create_app() -> FastAPI:
    """Create the backend app."""
    app = FastAPI(
        title="MAAT Intelligence Backend",
        version="2.0.0",
        description="MAAT — Truth from Chaos. Backend for case sync, OSINT enrichment, lead synthesis, and authority notification.",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    app.include_router(cases_router)
    app.include_router(sync_router)
    app.include_router(exports_router)
    app.include_router(investigations_router)
    return app


app = create_app()
