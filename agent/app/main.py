"""Application factory for the agent service."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

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
    # Serve the static in-Chatwoot dashboard apps (Knowledge Manager, Taxonomy
    # Manager, …) at /apps/<name>/. The directory is bind-mounted from the repo's
    # apps/ dir (compose). Guarded so the service still boots if it is absent.
    apps_dir = os.environ.get("APPS_DIR", "/srv/apps")
    if os.path.isdir(apps_dir):
        app.mount("/apps", StaticFiles(directory=apps_dir, html=True), name="apps")
    return app


app = create_app()
