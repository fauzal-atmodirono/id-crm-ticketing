"""Tests for `scripts.register_bot`: creates the agent bot via the Platform
API, reads back its secret via the account-scoped API, and assigns it to an
inbox via `POST /api/v1/accounts/{account}/inboxes/{inbox_id}/set_agent_bot`
(the route verified against `crm/chatwoot/config/routes.rb:259`).
"""

import httpx
import respx

from scripts import register_bot

CHATWOOT = "http://chatwoot-rails:3000"  # matches conftest env


@respx.mock
async def test_run_creates_bot_reads_secret_and_assigns_to_inbox(monkeypatch, capsys):
    monkeypatch.setenv("AGENT_PUBLIC_URL", "https://agent.example.com")

    create_bot = respx.post(f"{CHATWOOT}/platform/api/v1/agent_bots").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": 9,
                "name": "Gemini Agent",
                "outgoing_url": "https://agent.example.com/webhooks/chatwoot/bot",
                "access_token": "bot-access-token",
            },
        )
    )
    get_bot = respx.get(f"{CHATWOOT}/api/v1/accounts/1/agent_bots/9").mock(
        return_value=httpx.Response(200, json={"id": 9, "secret": "bot-secret"})
    )
    set_agent_bot = respx.post(
        f"{CHATWOOT}/api/v1/accounts/1/inboxes/3/set_agent_bot"
    ).mock(return_value=httpx.Response(200))

    exit_code = await register_bot._run(inbox_id=3)

    assert exit_code == 0
    assert create_bot.call_count == 1
    create_request = create_bot.calls.last.request
    assert b"webhooks/chatwoot/bot" in create_request.content
    assert b"Gemini Agent" in create_request.content

    assert get_bot.call_count == 1

    assert set_agent_bot.call_count == 1
    set_agent_bot_body = set_agent_bot.calls.last.request.content
    assert b'"agent_bot": 9' in set_agent_bot_body or b'"agent_bot":9' in set_agent_bot_body

    out = capsys.readouterr().out
    assert "CHATWOOT_BOT_TOKEN=bot-access-token" in out
    assert "CHATWOOT_BOT_SECRET=bot-secret" in out
    assert "Assigned bot 9 to inbox 3" in out


@respx.mock
async def test_run_warns_when_secret_is_not_readable(monkeypatch, capsys):
    monkeypatch.setenv("AGENT_PUBLIC_URL", "https://agent.example.com")

    respx.post(f"{CHATWOOT}/platform/api/v1/agent_bots").mock(
        return_value=httpx.Response(
            200, json={"id": 10, "access_token": "bot-access-token"}
        )
    )
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/agent_bots/10").mock(
        return_value=httpx.Response(403, json={"error": "forbidden"})
    )
    respx.post(f"{CHATWOOT}/api/v1/accounts/1/inboxes/3/set_agent_bot").mock(
        return_value=httpx.Response(200)
    )

    exit_code = await register_bot._run(inbox_id=3)

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "CHATWOOT_BOT_TOKEN=bot-access-token" in out
    assert "Could not read the bot's secret automatically" in out


async def test_run_fails_fast_without_agent_public_url(monkeypatch, capsys):
    monkeypatch.delenv("AGENT_PUBLIC_URL", raising=False)

    exit_code = await register_bot._run(inbox_id=3)

    assert exit_code == 1
    assert "AGENT_PUBLIC_URL is not set" in capsys.readouterr().err
