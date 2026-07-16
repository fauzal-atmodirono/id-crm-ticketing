from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, model_validator

# --- Chatwoot Webhook Schemas ---


class ChatwootSender(BaseModel):
    id: int | None = None
    name: str | None = None


class ChatwootConversation(BaseModel):
    id: int
    channel: str | None = None
    status: str | None = None
    inbox_id: int | None = None


class ChatwootWebhookPayload(BaseModel):
    event: str
    message_type: str | None = None
    content: str | None = None
    private: bool = False
    conversation: ChatwootConversation
    # Optional: conversation-level events (e.g. conversation_status_changed on
    # resolve) carry no message author, so Chatwoot omits `sender`. Requiring it
    # made those events fail validation (422) and silently skipped the resume.
    sender: ChatwootSender | None = None

    @model_validator(mode="before")
    @classmethod
    def _normalize_flat_conversation(cls, data: Any) -> Any:
        """Accept both Chatwoot payload shapes.

        Message events nest the conversation (`{event, message_type, content,
        conversation: {...}, sender: {...}}`). Conversation-level events
        (`conversation_status_changed` / `conversation_resolved`) send the
        conversation FLAT at the top level (`{event, id, status, inbox_id, ...}`)
        with no nested `conversation` — which otherwise 422s. Wrap the flat form.
        """
        if isinstance(data, dict) and "conversation" not in data and "id" in data:
            data = dict(data)
            data["conversation"] = {
                "id": data.get("id"),
                "status": data.get("status"),
                "inbox_id": data.get("inbox_id"),
                "channel": data.get("channel"),
            }
        return data


# --- Zendesk Webhook Schemas (Sunshine Conversations) ---


class ZendeskAuthor(BaseModel):
    role: Literal["user", "app", "business"]


class ZendeskContent(BaseModel):
    type: str
    text: str | None = None


class ZendeskMessage(BaseModel):
    id: str
    author: ZendeskAuthor
    content: ZendeskContent


class ZendeskConversation(BaseModel):
    id: str


class ZendeskEventPayload(BaseModel):
    conversation: ZendeskConversation
    message: ZendeskMessage


class ZendeskWebhookEvent(BaseModel):
    type: str  # e.g. "conversation:message"
    payload: ZendeskEventPayload


class ZendeskWebhookPayload(BaseModel):
    events: list[ZendeskWebhookEvent]


class ZendeskSupportWebhookPayload(BaseModel):
    session_id: str
    text: str
    author_name: str


class TtsRequest(BaseModel):
    text: str
    language: str = "en-US"


class ZendeskHandbackPayload(BaseModel):
    session_id: str
    status: str | None = None


class ZendeskEmailWebhookPayload(BaseModel):
    ticket_id: str
    text: str = ""
    requester_name: str | None = None
    requester_email: str | None = None
