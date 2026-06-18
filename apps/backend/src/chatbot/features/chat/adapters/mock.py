from __future__ import annotations

from chatbot.features.chat.models import KbArticle
from chatbot.features.chat.ports import ChatPort, KnowledgePort, TextToSpeechPort, TicketingPort


class InMemoryChatAdapter(ChatPort):
    """In-memory ChatPort for local development and testing."""

    def __init__(self) -> None:
        self.sent_messages: list[tuple[str, str]] = []

    async def send_message(self, conversation_id: str, text: str) -> None:
        self.sent_messages.append((conversation_id, text))


class InMemoryTicketingAdapter(TicketingPort):
    """In-memory TicketingPort simulating ticket lifecycles and handoff pauses."""

    def __init__(self) -> None:
        self.tickets: dict[str, dict[str, str]] = {}
        self.notes: dict[str, list[str]] = {}
        self.paused_sessions: set[str] = set()
        self.ticket_counter = 0

    async def create_ticket(self, session_id: str, title: str, body: str, urgency: str) -> str:
        self.ticket_counter += 1
        ticket_id = f"TKT-MOCK-{self.ticket_counter:03d}"
        self.tickets[ticket_id] = {
            "session_id": session_id,
            "title": title,
            "body": body,
            "urgency": urgency,
            "status": "new",
        }
        return ticket_id

    async def add_private_note(self, ticket_id: str, text: str) -> None:
        self.notes.setdefault(ticket_id, []).append(text)

    async def pause_ai_for_session(self, session_id: str) -> None:
        self.paused_sessions.add(session_id)

    async def unpause_ai_for_session(self, session_id: str) -> None:
        self.paused_sessions.discard(session_id)

    async def is_ai_paused(self, session_id: str) -> bool:
        return session_id in self.paused_sessions


class InMemoryKnowledgeAdapter(KnowledgePort):
    """In-memory KnowledgePort returning static mock support articles."""

    def __init__(self) -> None:
        self.articles = [
            KbArticle(
                title="Reset Password",
                content="To reset your password, click 'Forgot Password' on the login screen and follow the email link.",
                url="https://support.example.com/reset-password",
            ),
            KbArticle(
                title="App Crashing",
                content="If the application is crashing, try clearing the app cache or uninstalling and reinstalling it.",
                url="https://support.example.com/app-crashing",
            ),
        ]

    async def search_kb(self, query: str, limit: int = 2) -> list[KbArticle]:
        q = query.lower()
        results = [
            art for art in self.articles if q in art.title.lower() or q in art.content.lower()
        ]
        return results[:limit]


class MockVoiceAdapter(TextToSpeechPort):
    """Mock TTS adapter returning a deterministic byte payload, for local tests."""

    async def synthesize(self, text: str, language_code: str = "en-US") -> bytes:
        return f"mock_voice_audio:{language_code}:{text}".encode()
