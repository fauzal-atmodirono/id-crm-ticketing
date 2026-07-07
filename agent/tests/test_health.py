"""Tests for GET /healthz against a real (sqlite) async DB session."""

from httpx import ASGITransport, AsyncClient

from app.main import create_app


async def test_healthz_returns_200_ok():
    app = create_app()

    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
