from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

# --- Chatwoot Webhook Schemas ---


class ChatwootSender(BaseModel):
    id: int
    name: str | None = None


class ChatwootConversation(BaseModel):
    id: int
    channel: str | None = None


class ChatwootWebhookPayload(BaseModel):
    event: str
    message_type: str | None = None
    content: str | None = None
    conversation: ChatwootConversation
    sender: ChatwootSender


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
