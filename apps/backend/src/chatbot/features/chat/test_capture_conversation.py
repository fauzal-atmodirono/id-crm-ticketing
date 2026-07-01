from __future__ import annotations

from typing import Any

import pytest

from chatbot.features.chat.adapters.mock import (
    InMemoryChatAdapter,
    InMemoryKnowledgeAdapter,
    InMemoryTicketingAdapter,
    MockVoiceAdapter,
)
from chatbot.features.chat.ports import ConversationLogPort, ConversationLogResult
from chatbot.features.chat.service import OrchestratorService
from chatbot.platform.config import get_settings


class _LiveSession:
    """A session whose `.state` is a live dict (no copy-on-read)."""

    def __init__(self, state: dict[str, Any]) -> None:
        self.state = state


class _LiveSessions:
    """Session service test double returning the SAME live session object.

    The installed ADK InMemorySessionService deep-copies state on get_session,
    which defeats tests that simulate the agent persisting state. This double
    keeps a live reference so capture_conversation reads what the test sets.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, _LiveSession] = {}

    async def create_session(
        self, *, app_name: str, user_id: str, session_id: str, state: dict[str, Any]
    ) -> _LiveSession:
        session = _LiveSession(state)
        self._sessions[session_id] = session
        return session

    async def get_session(
        self, *, app_name: str, user_id: str, session_id: str
    ) -> _LiveSession | None:
        return self._sessions.get(session_id)


class _FakeLog(ConversationLogPort):
    def __init__(self) -> None:
        self.appended: list[tuple[str, str, str | None]] = []
        self.ensure_calls = 0
        self.rotate_calls = 0
        self.tags: list[tuple[str, str]] = []
        # Per-ticket append result; defaults to OK for any ticket not listed.
        self.results: dict[str, ConversationLogResult] = {}
        self._next_ticket = 2

    async def ensure_conversation_ticket(
        self, session_id: str, subject: str, customer_name: str | None, customer_phone: str | None
    ) -> str:
        self.ensure_calls += 1
        return "T1"

    async def rotate_conversation_ticket(
        self, session_id: str, subject: str, customer_name: str | None, customer_phone: str | None
    ) -> str:
        self.rotate_calls += 1
        ticket = f"T{self._next_ticket}"
        self._next_ticket += 1
        return ticket

    async def append_conversation_comment(
        self, ticket_id: str, text: str, status: str | None = None
    ) -> ConversationLogResult:
        self.appended.append((ticket_id, text, status))
        return self.results.get(ticket_id, ConversationLogResult.OK)

    async def add_ticket_tag(self, ticket_id: str, tag: str) -> None:
        self.tags.append((ticket_id, tag))

    async def post_public_reply(self, ticket_id: str, text: str, status: str | None = None) -> None:
        return None

    async def set_ticket_external_id(self, ticket_id: str, external_id: str) -> None:
        return None

    async def get_latest_public_comment(self, ticket_id: str) -> tuple[str, str | None, str | None]:
        return ("", None, None)


def _orchestrator(
    log: ConversationLogPort, sessions: _LiveSessions | None = None
) -> OrchestratorService:
    settings = get_settings()
    orch = OrchestratorService(
        settings=settings,
        chat_port=InMemoryChatAdapter(),
        ticketing_port=InMemoryTicketingAdapter(),
        knowledge_port=InMemoryKnowledgeAdapter(),
        tts_port=MockVoiceAdapter(),
        conversation_log_port=log,
        runner_factory=lambda _agent: None,
    )
    # Pin to a live-session double so the tests' direct state mutations (which
    # simulate what the agent tool / handle_turn persist in production)
    # round-trip. Both the dev .env's firestore store and ADK's in-memory store
    # deep-copy state on get_session, which would defeat the simulation. A shared
    # `sessions` lets two orchestrators model a restart against the same store.
    orch._adk_sessions = sessions or _LiveSessions()  # type: ignore[assignment]
    return orch


async def _seed_history(
    orch: OrchestratorService, session_id: str, msgs: list[dict[str, Any]]
) -> None:
    session = await orch._adk_sessions.create_session(
        app_name="chatbot",
        user_id=session_id,
        session_id=session_id,
        state={"session_id": session_id, "chat_history": msgs},
    )
    assert session is not None


@pytest.mark.asyncio
async def test_routine_conversation_logs_solved() -> None:
    log = _FakeLog()
    orch = _orchestrator(log)
    await _seed_history(
        orch,
        "whatsapp-+60123",
        [
            {"role": "user", "text": "what time do you open?"},
            {"role": "assistant", "text": "9am-6pm daily."},
        ],
    )

    await orch.capture_conversation("whatsapp-+60123", channel="WhatsApp")

    assert log.ensure_calls == 1
    assert len(log.appended) == 1
    _ticket, body, status = log.appended[0]
    assert status == "solved"
    assert "what time do you open?" in body


@pytest.mark.asyncio
async def test_flagged_conversation_logs_open() -> None:
    log = _FakeLog()
    orch = _orchestrator(log)
    await _seed_history(
        orch,
        "whatsapp-+60999",
        [
            {"role": "user", "text": "my car broke down, this is unacceptable"},
            {"role": "assistant", "text": "I'm sorry, let me help."},
        ],
    )
    # Simulate the gate firing via the agent tool.
    session = await orch._adk_sessions.get_session(
        app_name="chatbot", user_id="whatsapp-+60999", session_id="whatsapp-+60999"
    )
    assert session is not None
    session.state["ticket_flagged"] = True

    await orch.capture_conversation("whatsapp-+60999")

    assert log.appended[0][2] == "open"


@pytest.mark.asyncio
async def test_only_new_messages_appended() -> None:
    log = _FakeLog()
    orch = _orchestrator(log)
    await _seed_history(
        orch,
        "whatsapp-+60777",
        [
            {"role": "user", "text": "first"},
            {"role": "assistant", "text": "reply one"},
        ],
    )
    await orch.capture_conversation("whatsapp-+60777")

    session = await orch._adk_sessions.get_session(
        app_name="chatbot", user_id="whatsapp-+60777", session_id="whatsapp-+60777"
    )
    assert session is not None
    session.state["chat_history"].extend(
        [{"role": "user", "text": "second"}, {"role": "assistant", "text": "reply two"}]
    )
    await orch.capture_conversation("whatsapp-+60777")

    assert len(log.appended) == 2
    assert "second" in log.appended[1][1]
    assert "first" not in log.appended[1][1]


@pytest.mark.asyncio
async def test_ticket_reused_across_restart() -> None:
    log = _FakeLog()
    sessions = _LiveSessions()  # the persisted (Firestore-like) session store
    orch1 = _orchestrator(log, sessions)
    await _seed_history(
        orch1,
        "whatsapp-+60555",
        [{"role": "user", "text": "hi"}, {"role": "assistant", "text": "hello"}],
    )
    await orch1.capture_conversation("whatsapp-+60555")
    assert log.ensure_calls == 1

    # Simulate a backend restart: brand-new orchestrator (empty in-memory caches)
    # reading the SAME persisted session store.
    orch2 = _orchestrator(log, sessions)
    session = await sessions.get_session(
        app_name="chatbot", user_id="whatsapp-+60555", session_id="whatsapp-+60555"
    )
    assert session is not None
    session.state["chat_history"].extend(
        [{"role": "user", "text": "more"}, {"role": "assistant", "text": "sure"}]
    )
    await orch2.capture_conversation("whatsapp-+60555")

    # No new ticket created after the restart; only the new turn is appended.
    assert log.ensure_calls == 1
    assert len(log.appended) == 2
    assert "more" in log.appended[1][1]
    assert "hi" not in log.appended[1][1]


@pytest.mark.asyncio
async def test_closed_ticket_is_rotated_and_comment_retried() -> None:
    # A conversation pinned to a ticket that has since been CLOSED (Zendesk
    # rejects comments on it) must rotate to a fresh ticket and retry, so the
    # customer's messages still get mirrored.
    log = _FakeLog()
    log.results["49"] = ConversationLogResult.TICKET_CLOSED  # cached ticket is closed
    orch = _orchestrator(log)
    await _seed_history(
        orch,
        "whatsapp-+6281112117038",
        [{"role": "user", "text": "mau tanya proton S70"}],
    )
    session = await orch._adk_sessions.get_session(
        app_name="chatbot", user_id="whatsapp-+6281112117038", session_id="whatsapp-+6281112117038"
    )
    assert session is not None
    session.state["conversation_ticket_id"] = "49"  # pinned to the now-closed ticket

    await orch.capture_conversation("whatsapp-+6281112117038", channel="WhatsApp")

    # Rotated once, and the comment was retried against the fresh ticket.
    assert log.rotate_calls == 1
    assert [t for t, _b, _s in log.appended] == ["49", "T2"]
    # State now points at the new ticket and the turn is marked logged.
    assert session.state["conversation_ticket_id"] == "T2"
    assert session.state["conversation_logged_count"] == 1


@pytest.mark.asyncio
async def test_transient_failure_does_not_rotate_or_advance_count() -> None:
    # A transient (non-closed) failure must NOT spawn a replacement ticket, and
    # must leave the logged count unadvanced so the turn retries next time.
    log = _FakeLog()
    log.results["T1"] = ConversationLogResult.FAILED
    orch = _orchestrator(log)
    await _seed_history(
        orch,
        "whatsapp-+60123",
        [{"role": "user", "text": "hello"}],
    )

    await orch.capture_conversation("whatsapp-+60123", channel="WhatsApp")

    assert log.rotate_calls == 0
    assert len(log.appended) == 1
    session = await orch._adk_sessions.get_session(
        app_name="chatbot", user_id="whatsapp-+60123", session_id="whatsapp-+60123"
    )
    assert session is not None
    # Count NOT advanced → the unlogged message is retried on the next capture.
    assert int(session.state.get("conversation_logged_count", 0)) == 0
