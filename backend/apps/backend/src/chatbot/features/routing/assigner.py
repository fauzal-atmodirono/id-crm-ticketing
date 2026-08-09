"""Resolve a conversation's canonical channel and assign an agent — the Chatwoot
side of the Phase 5 /routing/assign endpoint. Mirrors PresenceFetcher's plumbing;
every request is fail-open (returns None) so a Chatwoot blip never raises."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from chatbot.features.routing.channels import canonical_channel

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


class RoutingAssigner:
    """Resolve a conversation's canonical channel and assign an agent.

    Self-contained: owns its own httpx client and constructs dual-auth headers
    (both ``api_access_token`` and ``Api-Access-Token``) from ``settings``
    directly. It is not coupled to ChatwootAdapter; ``main.py`` constructs it
    standalone with just the settings object.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _base(self) -> str:
        return (
            f"{self._settings.chatwoot_api_url.rstrip('/')}"
            f"/api/v1/accounts/{self._settings.chatwoot_account_id}"
        )

    async def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        # Deferred import avoids a circular dependency between the routing
        # package and the chat adapter package.
        import httpx  # noqa: PLC0415

        token = self._settings.chatwoot_api_token
        headers = {
            "Content-Type": "application/json",
            "api_access_token": token,
            "Api-Access-Token": token,
        }
        url = f"{self._base()}{path}"
        try:
            async with httpx.AsyncClient() as client:
                res = await client.request(method, url, json=payload, headers=headers, timeout=10.0)
                res.raise_for_status()
                return res.json() if res.content else {}
        except Exception as e:
            _log.error("routing_assigner_request_failed", method=method, path=path, error=str(e))
            return None

    async def resolve_channel(self, conversation_id: int) -> str:
        """Resolve a conversation's canonical channel.

        Calls GET /conversations/{id} to fetch the inbox_id, then GET /inboxes/{id}
        to fetch the channel_type, then maps it to a canonical channel via
        canonical_channel(). Returns "web" fail-open when any step fails or
        the conversation/inbox has no channel_type.
        """
        conv = await self._request("GET", f"/conversations/{conversation_id}")
        inbox_id = (conv or {}).get("inbox_id") if isinstance(conv, dict) else None
        if inbox_id is None:
            return "web"
        inbox = await self._request("GET", f"/inboxes/{inbox_id}")
        channel_type = inbox.get("channel_type") if isinstance(inbox, dict) else None
        return canonical_channel(channel_type)

    async def assign(self, conversation_id: int, agent_id: int) -> bool:
        """Assign an agent to a conversation. Returns whether Chatwoot took it.

        Calls POST /conversations/{id}/assignments with {"assignee_id": agent_id}.
        Still never raises -- `_request` swallows every failure mode (non-2xx,
        network error, timeout) and returns `None` -- so the two fail-open
        callers (`sweeper.py`'s background sweep and `/routing/assign`'s
        auto-pick branch) are unaffected by this signature: a Chatwoot blip
        must not start raising out of a background sweep.

        What changed, and why (review-final I5): the result used to be
        discarded here, so a caller could not tell a completed assignment from
        a refused one. The supervisor reassignment path writes an audit row
        whose whole purpose is to say who moved a case to whom (spec §3.7), and
        it was writing that row after a 422 -- an audit trail that records
        actions which never happened is worse than none, because it will be
        believed. Reporting the outcome is deliberately *additive* (an ignored
        return value) rather than an exception, so that only the caller that
        needs to care has to change.

        `is not None` rather than a truth test: `_request` returns `{}` for a
        successful response with an empty body, which is falsy but is a
        success.
        """
        res = await self._request(
            "POST", f"/conversations/{conversation_id}/assignments", {"assignee_id": agent_id}
        )
        return res is not None

    async def resolve_assignee(self, conversation_id: int) -> int | None:
        """Resolve a conversation's CURRENT Chatwoot assignee id.

        P6 task 5 (After-Call-Work) needs "which agent do I put into
        wrap-up" at call-end time, when nothing about the call itself
        (a static hunt-group `<Dial>`, no per-agent identity) can answer
        that. The conversation's live assignee is the one place that
        answer actually lives.

        Calls GET /conversations/{id} -- the same single-conversation
        fetch `resolve_channel` already uses -- and reads
        ``meta.assignee.id``, the exact path
        `PresenceFetcher.fetch_agent_open_counts`'s docstring documents
        (verified against a live Chatwoot) for a conversation object of
        this shape. Fails open to `None` on every failure mode: an
        unreachable Chatwoot, an unknown conversation, or a conversation
        with no assignee -- never a guess, per this task's explicit
        instruction not to invent an agent id.
        """
        conv = await self._request("GET", f"/conversations/{conversation_id}")
        if not isinstance(conv, dict):
            return None
        assignee = (conv.get("meta") or {}).get("assignee") or {}
        if not isinstance(assignee, dict):
            return None
        agent_id = assignee.get("id")
        return int(agent_id) if agent_id is not None else None
