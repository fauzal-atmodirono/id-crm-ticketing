"""Zammad -> Gemini draft-reply flow.

Called from `sync.on_ticket_event` for every Zammad ticket webhook. When the
event's article is a genuine new *customer* article (not one our own
integration user just posted, and not an internal agent note), draft a
suggested reply from the ticket's recent articles and post it back as an
internal (agent-only) note for a human to review before sending.

Gated by the `ZAMMAD_AI_DRAFTS` feature flag (default on). Like the rest of
the sync layer, this never raises out of `on_ticket_event` for expected
"nothing to do" cases or downstream failures — those are logged and skipped.
"""

import logging

import httpx

from app.ai import gemini
from app.clients.deps import get_zammad_client
from app.config import get_settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are a support agent for the company, drafting an internal suggested "
    "reply for a human agent to review before sending to the customer. Keep "
    "it short and strictly grounded in the ticket articles below — never "
    "invent facts, prices, policies, or commitments you can't verify from "
    "them."
)


def _is_new_customer_article(article: dict, settings) -> bool:
    if not article:
        return False

    created_by_login = (article.get("created_by") or {}).get("login")
    if created_by_login and created_by_login == settings.zammad_integration_login:
        return False

    # Zammad's ticket_article `sender` lookup value ("Customer"/"Agent"/
    # "System") is the reliable signal for who an article is from --
    # `created_by` is often a system/API user even for customer-submitted
    # articles (e.g. inbound email).
    return article.get("sender") == "Customer"


def _build_context(articles: object) -> str:
    article_list = articles if isinstance(articles, list) else []
    lines = [
        f"{article.get('sender', 'Unknown')}: {article.get('body') or ''}"
        for article in article_list[-20:]
    ]
    return "\n".join(lines) or "(no articles)"


async def draft_ticket_reply(payload: dict) -> None:
    settings = get_settings()
    if not settings.zammad_ai_drafts:
        return

    ticket = payload.get("ticket") or {}
    article = payload.get("article") or {}
    ticket_id = ticket.get("id")
    if ticket_id is None:
        return

    if not _is_new_customer_article(article, settings):
        return

    zammad = get_zammad_client()
    try:
        articles = await zammad.get_articles(ticket_id)
    except httpx.HTTPError:
        logger.exception(
            "draft_ticket_reply: failed to fetch articles for ticket %s", ticket_id
        )
        return

    context = _build_context(articles)

    try:
        text = gemini.generate(SYSTEM_PROMPT, context)
    except Exception:
        logger.exception(
            "draft_ticket_reply: gemini.generate failed for ticket %s", ticket_id
        )
        return

    if not text:
        return

    try:
        await zammad.add_article(
            ticket_id, f"🤖 AI suggested reply:\n\n{text}", internal=True
        )
    except httpx.HTTPError:
        logger.exception(
            "draft_ticket_reply: failed to post draft article for ticket %s",
            ticket_id,
        )
