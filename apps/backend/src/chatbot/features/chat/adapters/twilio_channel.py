from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
import structlog

from chatbot.features.chat.ports import ChatPort

if TYPE_CHECKING:
    from chatbot.platform.config import Settings

_log = structlog.get_logger(__name__)


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
        _log.info("sending_twilio_whatsapp_reply", to=conversation_id, has_media=bool(media_url))
        try:
            async with httpx.AsyncClient() as client:
                await self._post_message(client, conversation_id, text, media_url)
        except Exception as e:
            _log.error("twilio_whatsapp_reply_failed", to=conversation_id, error=str(e))
