"""Under RBAC the backend refuses the shared key on /rsa/*; the seeder must
say so plainly instead of surfacing a bare HTTP status error."""

from __future__ import annotations

import asyncio

import httpx
import pytest

import client


def _use_backend(handler):
    client._config = object()
    client._chatwoot = object()
    client._backend = httpx.AsyncClient(
        base_url="http://backend", transport=httpx.MockTransport(handler)
    )


@pytest.mark.parametrize("status", [401, 403])
def test_create_rsa_incident_refusal_is_actionable(status):
    _use_backend(lambda request: httpx.Response(status, json={"detail": "x"}))
    with pytest.raises(client.RsaAuthRefusedError, match="RBAC_ENABLED=false or a session"):
        asyncio.run(client.create_rsa_incident({"cause": "Flat Tyre"}))


def test_create_rsa_incident_still_works_when_rbac_off(monkeypatch):
    async def noop():
        return None

    monkeypatch.setattr(client, "_throttle", noop)
    _use_backend(lambda request: httpx.Response(200, json={"id": "abc"}))
    assert asyncio.run(client.create_rsa_incident({"cause": "Flat Tyre"})) == "abc"
