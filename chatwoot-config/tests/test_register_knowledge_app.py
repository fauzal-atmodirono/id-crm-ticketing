"""Unit tests for register_knowledge_app.py."""
from __future__ import annotations

import sys
from pathlib import Path

import httpx
import pytest
import respx

sys.path.insert(0, str(Path(__file__).parent.parent))
from register_knowledge_app import register_dashboard_app, DashboardAppConfig

BASE = "http://chatwoot-test"
ACCOUNT_ID = 1
TOKEN = "test-token"
APPS_URL = f"{BASE}/api/v1/accounts/{ACCOUNT_ID}/dashboard_apps"


@respx.mock
def test_creates_app_when_not_existing():
    respx.get(APPS_URL).mock(return_value=httpx.Response(200, json=[]))
    create_route = respx.post(APPS_URL).mock(
        return_value=httpx.Response(200, json={"id": 2, "title": "Knowledge"})
    )
    cfg = DashboardAppConfig(
        title="Knowledge",
        url="http://agent.1-2-3-4.nip.io/apps/knowledge-manager?backend=https%3A%2F%2Fproton.example.com&key=secret",
    )
    result = register_dashboard_app(BASE, TOKEN, ACCOUNT_ID, cfg, dry_run=False)
    assert result == "created"
    assert create_route.called
    # Verify POST body shape
    posted = create_route.calls[0].request
    import json
    body = json.loads(posted.content)
    assert body["title"] == "Knowledge"
    assert body["content"][0]["type"] == "frame"
    url = body["content"][0]["url"]
    assert "/apps/knowledge-manager" in url
    assert "backend=" in url
    assert "key=" in url


@respx.mock
def test_skips_when_app_already_exists():
    respx.get(APPS_URL).mock(
        return_value=httpx.Response(200, json=[{"id": 2, "title": "Knowledge"}])
    )
    create_route = respx.post(APPS_URL).mock(return_value=httpx.Response(200, json={}))
    cfg = DashboardAppConfig(
        title="Knowledge",
        url="http://agent.1-2-3-4.nip.io/apps/knowledge-manager?backend=https%3A%2F%2Fproton.example.com&key=secret",
    )
    result = register_dashboard_app(BASE, TOKEN, ACCOUNT_ID, cfg, dry_run=False)
    assert result == "unchanged"
    assert not create_route.called


@respx.mock
def test_dry_run_does_not_create():
    respx.get(APPS_URL).mock(return_value=httpx.Response(200, json=[]))
    create_route = respx.post(APPS_URL).mock(return_value=httpx.Response(200, json={}))
    cfg = DashboardAppConfig(
        title="Knowledge",
        url="http://agent/apps/knowledge-manager?backend=https%3A%2F%2Fproton.example.com&key=secret",
    )
    result = register_dashboard_app(BASE, TOKEN, ACCOUNT_ID, cfg, dry_run=True)
    assert result == "created"
    assert not create_route.called
