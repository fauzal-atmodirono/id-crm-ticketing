from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
import structlog

from chatbot.features.chat.ports import ChatPort

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)

# Twilio rejects a WhatsApp message whose Body exceeds 1600 characters with a
# 400 (error 21617). Long AI answers must be split into several messages.
_WHATSAPP_BODY_LIMIT = 1600


def _chunk_whatsapp_body(text: str, limit: int = _WHATSAPP_BODY_LIMIT) -> list[str]:
    """Split a reply into pieces that each fit Twilio's WhatsApp body limit.

    Prefers to break at a paragraph/newline, then a space, and only hard-cuts a
    run with no whitespace as a last resort — so words and paragraphs stay intact
    whenever possible. Returns [] for empty/whitespace-only input.
    """
    text = text.strip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        window = remaining[:limit]
        split = window.rfind("\n")
        if split < limit // 2:
            split = window.rfind(" ")
        if split < limit // 2:
            split = limit  # unbroken run → hard cut
        chunk = remaining[:split].strip()
        if chunk:
            chunks.append(chunk)
        remaining = remaining[split:].strip()
    if remaining:
        chunks.append(remaining)
    return chunks


class TwilioChannelAdapter(ChatPort):
    """Sends outbound WhatsApp messages through the Twilio REST API.

    `conversation_id` is the customer's WhatsApp address ("whatsapp:+E164").
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def _messages_url(self) -> str:
        return (
            "https://api.twilio.com/2010-04-01/Accounts/"
            f"{self._settings.twilio_account_sid}/Messages.json"
        )

    async def _post_message(
        self, client: Any, to: str, text: str, media_url: str | None = None
    ) -> None:
        data = {
            "From": self._settings.twilio_whatsapp_number,
            "To": to,
            "Body": text,
        }
        if media_url:
            # WhatsApp renders the image as a card with the Body as its caption.
            data["MediaUrl"] = media_url
        res = await client.post(
            self._messages_url(),
            data=data,
            auth=(self._settings.twilio_account_sid, self._settings.twilio_auth_token),
            timeout=10.0,
        )
        res.raise_for_status()

    async def send_message(
        self, conversation_id: str, text: str, media_url: str | None = None
    ) -> None:
        # A reply over 1600 chars is rejected wholesale by Twilio, so split it
        # into ordered messages. Media rides on the first message only; the rest
        # are text continuations.
        chunks = _chunk_whatsapp_body(text)
        if not chunks:
            chunks = [text] if media_url else []
        _log.info(
            "sending_twilio_whatsapp_reply",
            to=conversation_id,
            has_media=bool(media_url),
            parts=len(chunks),
        )
        try:
            async with httpx.AsyncClient() as client:
                for i, chunk in enumerate(chunks):
                    await self._post_message(
                        client, conversation_id, chunk, media_url if i == 0 else None
                    )
        except httpx.HTTPStatusError as e:
            # str(e) is httpx's generic message; the Twilio error code/reason
            # lives in the response body — log it so failures are diagnosable.
            _log.error(
                "twilio_whatsapp_reply_failed",
                to=conversation_id,
                error=str(e),
                twilio_response=e.response.text,
            )
        except Exception as e:
            _log.error("twilio_whatsapp_reply_failed", to=conversation_id, error=str(e))
