"""Tests for `app.services.responder.draft_ticket_reply`: a genuine new
customer article gets an internal AI-drafted reply posted back; an article
authored by our own integration user (loop prevention) does not."""

import httpx
import respx

from app.ai import gemini
from app.config import get_settings
from app.services import responder

ZAMMAD = "http://zammad-nginx:8080"

ARTICLES_RESPONSE = [
    {"sender": "Customer", "body": "My order hasn't arrived yet."},
    {"sender": "Agent", "body": "Let me check on that for you."},
]


@respx.mock
async def test_customer_article_gets_an_internal_draft_reply(monkeypatch):
    respx.get(f"{ZAMMAD}/api/v1/ticket_articles/by_ticket/501").mock(
        return_value=httpx.Response(200, json=ARTICLES_RESPONSE)
    )
    add_article = respx.post(f"{ZAMMAD}/api/v1/ticket_articles").mock(
        return_value=httpx.Response(201, json={"id": 1})
    )

    monkeypatch.setattr(gemini, "generate", lambda system_prompt, context, client=None: "Your order is on its way.")

    payload = {
        "ticket": {"id": 501, "number": "10501", "state": "open"},
        "article": {"sender": "Customer", "created_by": {"login": "jane@example.com"}},
    }
    await responder.draft_ticket_reply(payload)

    assert add_article.call_count == 1
    body = add_article.calls.last.request.content
    assert b"AI suggested reply" in body
    assert b"Your order is on its way." in body
    assert b'"internal": true' in body or b'"internal":true' in body


@respx.mock
async def test_integration_authored_article_gets_no_draft():
    settings = get_settings()
    articles_route = respx.get(f"{ZAMMAD}/api/v1/ticket_articles/by_ticket/502")
    add_article = respx.post(f"{ZAMMAD}/api/v1/ticket_articles")

    payload = {
        "ticket": {"id": 502, "number": "10502", "state": "open"},
        "article": {
            "sender": "Agent",
            "created_by": {"login": settings.zammad_integration_login},
        },
    }
    await responder.draft_ticket_reply(payload)

    assert not articles_route.called
    assert not add_article.called


@respx.mock
async def test_agent_authored_article_gets_no_draft():
    """A human agent's reply (sender=Agent, not our integration login) also
    shouldn't get an AI draft -- only genuine customer articles do."""
    articles_route = respx.get(f"{ZAMMAD}/api/v1/ticket_articles/by_ticket/503")
    add_article = respx.post(f"{ZAMMAD}/api/v1/ticket_articles")

    payload = {
        "ticket": {"id": 503, "number": "10503", "state": "open"},
        "article": {"sender": "Agent", "created_by": {"login": "human.agent@example.com"}},
    }
    await responder.draft_ticket_reply(payload)

    assert not articles_route.called
    assert not add_article.called


@respx.mock
async def test_feature_flag_disabled_skips_drafting(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "zammad_ai_drafts", False)

    articles_route = respx.get(f"{ZAMMAD}/api/v1/ticket_articles/by_ticket/504")
    add_article = respx.post(f"{ZAMMAD}/api/v1/ticket_articles")

    payload = {
        "ticket": {"id": 504, "number": "10504", "state": "open"},
        "article": {"sender": "Customer"},
    }
    await responder.draft_ticket_reply(payload)

    assert not articles_route.called
    assert not add_article.called
