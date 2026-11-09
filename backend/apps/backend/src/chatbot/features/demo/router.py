"""/demo/* -- the test console's Demo tab (persona cards, Seed, Reset).

Registered by main.py ONLY when ``DEMO_CONSOLE_ENABLED`` is true, so a
tenant that never turns it on has no such route at all.

Access: on the VM these routes are reachable only through the test
console's Caddy block (``/apps/test-console/api/*``, HTTP basic auth, path
stripped); ``/demo/*`` is not in any vhost's public backend matcher. The
route itself adds two browser-side checks on top: a request carrying an
``Origin`` must come from ``FRONTEND_ORIGINS``, and the writes need an
``X-Demo-Console: 1`` header (a cross-site form or a no-preflight fetch
cannot set it). Seed and reset are serialised by one lock per process.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import TYPE_CHECKING, Any

import structlog
from fastapi import APIRouter, HTTPException, Request

from chatbot.features.demo.crm import ChatwootError
from chatbot.features.demo.safety import DemoSafetyError

if TYPE_CHECKING:
    from chatbot.features.demo.seeder import DemoSeeder
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

SeederFactory = Callable[[], AbstractAsyncContextManager["DemoSeeder"]]


def build_demo_router(settings: Settings, seeder_factory: SeederFactory) -> APIRouter:
    router = APIRouter(prefix="/demo", tags=["demo"])
    lock = asyncio.Lock()

    def _check(request: Request, *, write: bool) -> None:
        origin = request.headers.get("origin")
        if origin is not None and origin not in settings.frontend_origins:
            _log.warning("demo_console_origin_rejected", origin=origin)
            raise HTTPException(status_code=403, detail="Origin not allowed")
        if write and request.headers.get("x-demo-console") != "1":
            raise HTTPException(status_code=403, detail="Missing X-Demo-Console header")

    async def _run(action: str) -> Any:
        try:
            async with seeder_factory() as seeder:
                if action == "status":
                    return await seeder.status()
                async with lock:
                    return await (seeder.seed() if action == "seed" else seeder.reset())
        except DemoSafetyError as exc:
            _log.warning("demo_console_refused", action=action, reason=str(exc))
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ChatwootError as exc:
            _log.warning("demo_console_crm_failed", action=action, reason=str(exc))
            raise HTTPException(status_code=502, detail=f"CRM request failed: {exc}") from exc

    @router.get("/personas")
    async def personas(request: Request) -> Any:
        _check(request, write=False)
        return await _run("status")

    @router.post("/seed")
    async def seed(request: Request) -> Any:
        _check(request, write=True)
        return await _run("seed")

    @router.post("/reset")
    async def reset(request: Request) -> Any:
        _check(request, write=True)
        return await _run("reset")

    return router
