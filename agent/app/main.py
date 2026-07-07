"""Application factory for the agent service.

Webhook routers (Chatwoot, Zammad) are Task 4's responsibility — they get
registered in `create_app` alongside the health router once they exist.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_settings
from app.db.session import init_db
from app.routers import health


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await init_db()
    yield


def create_app() -> FastAPI:
    get_settings()  # fail fast if required env vars are missing
    app = FastAPI(title="Agent Service", lifespan=lifespan)
    app.include_router(health.router)
    # Task 4: app.include_router(chatwoot_webhooks.router)
    # Task 4: app.include_router(zammad_webhooks.router)
    return app


app = create_app()
