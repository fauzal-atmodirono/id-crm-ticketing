"""A2A server for one Logisly agent, for Cloud Run and Gemini Enterprise.

LOGISLY_AGENT=uc2|uc3 picks the agent. LOGISLY_PUBLIC_URL is the URL written
into the agent card (the Cloud Run service URL); it defaults to localhost.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

import uvicorn
from starlette.requests import Request
from starlette.responses import JSONResponse

from logisly_agents.config import configure_vertex_env

configure_vertex_env()


def _root_agent(which: str):
    if which == "uc2":
        from logisly_agents.uc2.agent import root_agent
    elif which == "uc3":
        from logisly_agents.uc3.agent import root_agent
    else:
        raise SystemExit(f"error: LOGISLY_AGENT must be uc2 or uc3, got {which!r}")
    return root_agent


def build_app():
    from google.adk.a2a.utils.agent_to_a2a import to_a2a

    which = os.environ.get("LOGISLY_AGENT", "").strip().lower()
    port = int(os.environ.get("PORT", "8080"))
    public = urlparse(os.environ.get("LOGISLY_PUBLIC_URL") or f"http://localhost:{port}")
    protocol = public.scheme or "http"
    host = public.hostname or "localhost"
    public_port = public.port or (443 if protocol == "https" else 80)
    app = to_a2a(_root_agent(which), host=host, port=public_port, protocol=protocol)

    async def healthz(_: Request) -> JSONResponse:
        return JSONResponse({"ok": True})

    app.add_route("/healthz", healthz, methods=["GET"])
    return app


def main() -> None:
    uvicorn.run(build_app(), host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))  # noqa: S104


if __name__ == "__main__":
    main()
