"""A2A server for one Logisly agent, for Cloud Run and Gemini Enterprise.

LOGISLY_AGENT=uc2|uc3 picks the agent. LOGISLY_PUBLIC_URL is the URL written
into the agent card (the Cloud Run service URL); it defaults to localhost.
"""

from __future__ import annotations

import asyncio
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


INPUT_MODES = [
    "text/plain",
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "message/rfc822",
    "image/png",
    "image/jpeg",
]


# One short skill per agent. ADK's builder emits the full instruction plus one skill per tool,
# which only adds noise for Gemini Enterprise's agent routing.
SKILLS = {
    "logisly_purchasing": {
        "name": "Seleksi penawaran transporter",
        "description": (
            "Mencatat dokumen tender dan penawaran transporter, membandingkannya dengan baseline "
            "harga dan aturan bisnis, lalu merekomendasikan transporter per lane."
        ),
        "tags": ["purchasing", "tender", "penawaran", "logistik"],
    },
    "logisly_product_manager": {
        "name": "PRD dan backlog produk",
        "description": (
            "Mengubah permintaan, notulen, email dan chat menjadi PRD, memeriksa konflik dan duplikat "
            "di backlog GitHub, mengecek kapasitas tim, dan mengusulkan perubahan issue."
        ),
        "tags": ["product", "prd", "backlog", "github"],
    },
}


def build_card(agent, rpc_url: str):
    """ADK's builder hard-codes text/plain input; advertise the uploads users actually send."""
    from a2a.types import AgentSkill
    from google.adk.a2a.utils.agent_card_builder import AgentCardBuilder

    card = asyncio.run(AgentCardBuilder(agent=agent, rpc_url=rpc_url).build())
    card.default_input_modes = list(INPUT_MODES)
    meta = SKILLS[agent.name]
    card.skills = [
        AgentSkill(
            id=agent.name,
            name=meta["name"],
            description=meta["description"],
            tags=list(meta["tags"]),
            input_modes=list(INPUT_MODES),
        )
    ]
    return card


def build_app():
    from google.adk.a2a.utils.agent_to_a2a import to_a2a

    which = os.environ.get("LOGISLY_AGENT", "").strip().lower()
    port = int(os.environ.get("PORT", "8080"))
    public = urlparse(os.environ.get("LOGISLY_PUBLIC_URL") or f"http://localhost:{port}")
    protocol = public.scheme or "http"
    host = public.hostname or "localhost"
    public_port = public.port or (443 if protocol == "https" else 80)
    agent = _root_agent(which)
    rpc_url = f"{protocol}://{host}:{public_port}/"
    card = build_card(agent, rpc_url)
    app = to_a2a(agent, host=host, port=public_port, protocol=protocol, agent_card=card)

    async def healthz(_: Request) -> JSONResponse:
        return JSONResponse({"ok": True})

    app.add_route("/healthz", healthz, methods=["GET"])
    return app


def main() -> None:
    uvicorn.run(build_app(), host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))  # noqa: S104


if __name__ == "__main__":
    main()
