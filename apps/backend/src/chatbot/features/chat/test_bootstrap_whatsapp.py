from __future__ import annotations

from chatbot.main import bootstrap_application


def test_twilio_whatsapp_route_is_registered() -> None:
    app = bootstrap_application()
    # FastAPI nests included routers rather than flattening app.routes, so assert
    # against the OpenAPI path table, which walks all effective routes.
    assert "/webhooks/twilio-whatsapp" in app.openapi()["paths"]
