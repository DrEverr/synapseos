"""SynapseOS Web Demo — FastAPI application.

Entry point: ``synapse-web`` or ``uvicorn synapse.web.app:create_app --factory``.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.staticfiles import StaticFiles

from synapse.web.deps import close_dependencies, init_dependencies, verify_credentials

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


def create_app() -> FastAPI:
    """FastAPI application factory."""
    app = FastAPI(
        title="SynapseOS Wacker Demo",
        version="0.5.0",
        docs_url="/docs",
    )

    # Global auth dependency for all API routes
    auth_dep = Depends(verify_credentials)

    @app.on_event("startup")
    async def startup() -> None:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
        init_dependencies()
        logger.info("SynapseOS Web Demo started")

    @app.on_event("shutdown")
    async def shutdown() -> None:
        close_dependencies()

    # Include routers
    from synapse.web.routers.chat import router as chat_router
    from synapse.web.routers.documents import router as documents_router
    from synapse.web.routers.graph import router as graph_router
    from synapse.web.routers.review import router as review_router

    app.include_router(graph_router, prefix="/api", dependencies=[auth_dep])
    app.include_router(review_router, prefix="/api", dependencies=[auth_dep])
    app.include_router(documents_router, prefix="/api", dependencies=[auth_dep])
    app.include_router(chat_router, prefix="/api")  # WS handles auth separately

    # Serve static files (frontend)
    app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")

    return app


def main() -> None:
    """CLI entry point for synapse-web."""
    import uvicorn

    uvicorn.run(
        "synapse.web.app:create_app",
        host="0.0.0.0",
        port=8000,
        factory=True,
        reload=True,
    )
