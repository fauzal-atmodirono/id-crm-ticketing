"""Application factory for the agent service."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.clients.deps import aclose_clients
from app.config import get_settings
from app.db.session import init_db
from app.routers import chatwoot as chatwoot_webhooks
from app.routers import health
from app.routers import zammad as zammad_webhooks


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    await init_db()
    yield
    # Task 4 handoff: httpx clients are lazily-constructed singletons
    # (app.clients.deps) so their lifecycle is owned end-to-end here rather
    # than left dangling for the process/GC to clean up.
    await aclose_clients()


def create_app() -> FastAPI:
    get_settings()  # fail fast if required env vars are missing
    app = FastAPI(title="Agent Service", lifespan=lifespan)
    app.include_router(health.router)
    app.include_router(chatwoot_webhooks.router)
    app.include_router(zammad_webhooks.router)
    return app


app = create_app()
