from __future__ import annotations

from typing import Protocol

from chatbot.features.chat.models import KbArticle, VoiceTranscript


class ChatPort(Protocol):
    """Port interface for sending messages back to the customer's chat interface."""

    async def send_message(self, conversation_id: str, text: str) -> None:
        """Send a plain text response back to the customer."""
        ...


class TicketingPort(Protocol):
    """Port interface for ticketing and customer escalation systems (Zammad, Zendesk Support)."""

    async def create_ticket(self, session_id: str, title: str, body: str, urgency: str) -> str:
        """Create a new customer ticket. Returns the created ticket's ID."""
        ...

    async def add_private_note(self, ticket_id: str, text: str) -> None:
        """Post a private note or internal comment (context banner) in the ticket."""
        ...

    async def pause_ai_for_session(self, session_id: str) -> None:
        """State mutation: pause AI response actions on this session due to handoff."""
        ...

    async def unpause_ai_for_session(self, session_id: str) -> None:
        """State mutation: resume AI response actions on this session."""
        ...

    async def is_ai_paused(self, session_id: str) -> bool:
        """Check if AI is paused for this session (e.g. human is currently handling it)."""
        ...


class KnowledgePort(Protocol):
    """Port interface for retrieving articles from a Knowledge Base (Zendesk Guide, DB)."""

    async def search_kb(self, query: str, limit: int = 2) -> list[KbArticle]:
        """Search documentation articles matching query."""
        ...


class SpeechToTextPort(Protocol):
    """Port interface for transcribing audio clips to text."""

    async def transcribe(
        self, audio_content: bytes, language_code: str = "en-US"
    ) -> VoiceTranscript:
        """Convert voice audio bytes into a text transcript."""
        ...


class TextToSpeechPort(Protocol):
    """Port interface for synthesizing text into audio speech."""

    async def synthesize(self, text: str, language_code: str = "en-US") -> bytes:
        """Synthesize text into audio bytes (e.g. MP3 or WAV)."""
        ...
