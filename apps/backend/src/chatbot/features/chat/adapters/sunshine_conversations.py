"""Sunshine Conversations (v2) bridge adapter.

Zendesk Messaging is built on Sunshine Conversations — this adapter uses the
SC REST API directly so the customer can stay inside the custom Vue chat UI
while a Zendesk agent replies from their workspace.

Auth is HTTP Basic with the SC integration's key id + secret. The webhook
secret (separate from the API secret) verifies inbound payloads: SC v2 sends
the raw shared secret as the `X-Api-Key` header, which we compare in constant
time. We also accept an HMAC-SHA256 of the body for legacy (Smooch v1)
back-compat.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from datetime import UTC, datetime
from http import HTTPStatus
from typing import TYPE_CHECKING, Any, cast

import httpx
import structlog

from chatbot.features.chat.models import AgentMessageEvent, HandoffOpenPayload
from chatbot.features.chat.ports import HumanAgentBridgePort

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

# Sunshine Conversations webhook signature header name.
_SC_SIGNATURE_HEADER_LOWER = "x-api-key"


class SunshineConversationsAdapter(HumanAgentBridgePort):
    """REST-based bridge to Sunshine Conversations v2."""

    BASE = "https://api.smooch.io/v2"
    # Sunshine rejects a message whose content.text is longer than this.
    _MAX_MESSAGE_CHARS = 4096

    def __init__(self, settings: Settings) -> None:
        self._app_id = settings.zendesk_app_id
        self._key_id = settings.zendesk_key_id
        self._secret = settings.zendesk_secret_key
        self._webhook_secret = settings.sunshine_webhook_secret

        auth_str = f"{self._key_id}:{self._secret}".encode()
        self._auth_header = "Basic " + base64.b64encode(auth_str).decode("ascii")

    # --- Outbound -------------------------------------------------------------

    async def open_handoff(self, payload: HandoffOpenPayload) -> str:
        """Ensure the user exists, create a conversation, post the AI summary."""
        if not self._app_id:
            raise RuntimeError("ZENDESK_APP_ID is not configured; cannot open handoff.")

        external_id = payload.session_id

        async with httpx.AsyncClient(timeout=10.0) as client:
            await self._upsert_user(client, payload, external_id)
            conversation_id = await self._create_conversation(client, external_id)
            try:
                await self._post_business_summary(client, conversation_id, payload)
            except Exception as e:
                # The live bridge is already usable (user + conversation exist); a
                # failed summary message must NOT collapse the handoff to ticket-only.
                _log.error(
                    "sunshine_post_business_summary_skipped",
                    conversation_id=conversation_id,
                    error=str(e),
                )

        _log.info(
            "sunshine_handoff_opened",
            session_id=payload.session_id,
            conversation_id=conversation_id,
        )
        return conversation_id

    async def forward_customer_message(
        self, conversation_id: str, user_external_id: str, text: str
    ) -> None:
        if not self._app_id:
            raise RuntimeError("ZENDESK_APP_ID is not configured.")

        url = f"{self.BASE}/apps/{self._app_id}/conversations/{conversation_id}/messages"
        body = {
            "author": {"type": "user", "userExternalId": user_external_id},
            "content": {"type": "text", "text": text},
        }
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.post(url, json=body, headers=self._headers())
            if res.status_code >= HTTPStatus.BAD_REQUEST:
                _log.error(
                    "sunshine_forward_failed",
                    conversation_id=conversation_id,
                    status=res.status_code,
                    body=res.text[:300],
                )
                res.raise_for_status()

    # --- Inbound webhook ------------------------------------------------------

    def verify_webhook_signature(self, body: bytes, signature: str | None) -> bool:
        if not self._webhook_secret:
            # No secret configured — accept but log so it's visible in dev.
            _log.warning("sunshine_webhook_signature_unverified_no_secret")
            return True
        if not signature:
            return False
        # Sunshine Conversations v2 sends the raw shared secret directly as the
        # X-Api-Key header value. We also accept an HMAC-SHA256 of the body for
        # back-compat with the legacy (Smooch v1) signing scheme. Both paths
        # require knowledge of the secret, so accepting either does not weaken
        # verification — it only avoids rejecting legitimate v2 traffic.
        if hmac.compare_digest(signature, self._webhook_secret):
            return True
        expected = hmac.new(self._webhook_secret.encode(), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    def parse_webhook_events(self, payload: dict[str, object]) -> list[AgentMessageEvent]:
        """Extract business-authored text messages from a webhook body.

        The customer's own messages also flow through the webhook stream; we
        skip those (we already showed them in our UI when the user pressed
        Send). Only `business`-author events — i.e. messages from the Zendesk
        agent or from another bot — are forwarded.
        """
        events_raw = payload.get("events")
        if not isinstance(events_raw, list):
            return []

        out: list[AgentMessageEvent] = []
        for raw in events_raw:
            if not isinstance(raw, dict):
                continue
            event = cast(dict[str, Any], raw)
            event_type = event.get("type")
            if event_type != "conversation:message":
                continue

            body = event.get("payload")
            if not isinstance(body, dict):
                continue

            message = body.get("message")
            conversation = body.get("conversation")
            if not isinstance(message, dict) or not isinstance(conversation, dict):
                continue

            author = message.get("author")
            if not isinstance(author, dict):
                continue
            if author.get("type") != "business":
                # Customer or system echo — ignore.
                continue

            content = message.get("content")
            if not isinstance(content, dict) or content.get("type") != "text":
                continue
            text = content.get("text")
            if not isinstance(text, str) or not text.strip():
                continue

            conversation_id = conversation.get("id")
            if not isinstance(conversation_id, str):
                continue

            received_at = _parse_iso(message.get("received") or event.get("createdAt"))

            out.append(
                AgentMessageEvent(
                    conversation_id=conversation_id,
                    author_name=str(author.get("displayName") or "Support agent"),
                    text=text,
                    timestamp=received_at,
                )
            )

        return out

    # --- Internal helpers -----------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": self._auth_header,
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def _upsert_user(
        self, client: httpx.AsyncClient, payload: HandoffOpenPayload, external_id: str
    ) -> None:
        # POST /users is idempotent on externalId — Sunshine will 409 if the user
        # already exists; treat that as success.
        url = f"{self.BASE}/apps/{self._app_id}/users"
        # Sunshine's user `profile` only persists givenName/surname/email/avatarUrl/
        # locale — `phones` and `metadata` are NOT profile fields and are silently
        # dropped if nested there. Phone and preferred_model must travel as
        # top-level user `metadata` instead so the agent can see them.
        profile: dict[str, Any] = {
            "givenName": payload.customer_name,
            "email": payload.customer_email,
            "locale": payload.language if payload.language != "unknown" else "en",
        }
        metadata: dict[str, Any] = {}
        if payload.customer_phone:
            metadata["customer_phone"] = payload.customer_phone
        if payload.preferred_model:
            metadata["preferred_model"] = payload.preferred_model

        body: dict[str, Any] = {
            "externalId": external_id,
            "profile": profile,
        }
        if metadata:
            body["metadata"] = metadata
        res = await client.post(url, json=body, headers=self._headers())
        if res.status_code == HTTPStatus.CONFLICT:
            # User already exists, update their profile + metadata
            patch_url = f"{self.BASE}/apps/{self._app_id}/users/{external_id}"
            patch_body: dict[str, Any] = {"profile": profile}
            if metadata:
                patch_body["metadata"] = metadata
            patch_res = await client.patch(patch_url, json=patch_body, headers=self._headers())
            if patch_res.status_code >= HTTPStatus.BAD_REQUEST:
                _log.error(
                    "sunshine_update_user_failed",
                    status=patch_res.status_code,
                    body=patch_res.text[:300],
                )
                patch_res.raise_for_status()
            return
        if res.status_code >= HTTPStatus.BAD_REQUEST:
            _log.error("sunshine_upsert_user_failed", status=res.status_code, body=res.text[:300])
            res.raise_for_status()

    async def _create_conversation(self, client: httpx.AsyncClient, external_id: str) -> str:
        url = f"{self.BASE}/apps/{self._app_id}/conversations"
        body = {
            "type": "personal",
            "participants": [{"userExternalId": external_id, "subscribeSDK": True}],
            "displayName": "AI escalation",
        }
        res = await client.post(url, json=body, headers=self._headers())
        if res.status_code >= HTTPStatus.BAD_REQUEST:
            _log.error(
                "sunshine_create_conversation_failed",
                status=res.status_code,
                body=res.text[:300],
            )
            res.raise_for_status()

        data = res.json()
        conversation = data.get("conversation", {})
        conv_id = conversation.get("id")
        if not isinstance(conv_id, str):
            raise RuntimeError(f"Sunshine returned no conversation id: {data}")
        return conv_id

    async def _post_business_summary(
        self,
        client: httpx.AsyncClient,
        conversation_id: str,
        payload: HandoffOpenPayload,
    ) -> None:
        header = (
            f"AI handoff for session {payload.session_id}\n"
            f"Urgency: {payload.urgency.upper()} · Language: {payload.language}\n"
            f"Summary: {payload.ai_summary}\n\n"
            "Recent conversation with the AI assistant:\n\n"
        )

        # Sunshine rejects messages whose content.text exceeds 4096 characters.
        # Keep the MOST RECENT transcript lines that fit under a safe budget — the
        # full transcript is also persisted to Firestore via handoff_bridge.register.
        budget = self._MAX_MESSAGE_CHARS - len(header)
        kept: list[str] = []
        used = 0
        truncated = False
        for msg in reversed(payload.transcript):
            line = f"- {msg.role.upper()}: {msg.text}"
            if used + len(line) + 1 > budget:
                truncated = True
                break
            kept.append(line)
            used += len(line) + 1
        kept.reverse()
        if truncated:
            kept.insert(0, "[… earlier messages truncated …]")

        summary_message = header + "\n".join(kept)

        url = f"{self.BASE}/apps/{self._app_id}/conversations/{conversation_id}/messages"
        body = {
            "author": {"type": "user", "userExternalId": payload.session_id},
            "content": {"type": "text", "text": summary_message},
        }
        res = await client.post(url, json=body, headers=self._headers())
        if res.status_code >= HTTPStatus.BAD_REQUEST:
            _log.error(
                "sunshine_post_business_summary_failed",
                status=res.status_code,
                body=res.text[:300],
            )
            res.raise_for_status()


def _parse_iso(value: object) -> datetime:
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(UTC)


__all__ = ["_SC_SIGNATURE_HEADER_LOWER", "SunshineConversationsAdapter"]
