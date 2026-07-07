"""Tests for `app.services.orchestrator.handle_bot_event`: eligibility
filtering, the three decision types' side effects in both `suggest` and
`auto` AGENT_MODE, the `ai_actions` audit row written per decision, the
debounce coalescing/cancellation semantics, and downstream-failure logging.

The debounce delay is monkeypatched to 0 so tests don't sleep 3 real
seconds — `handle_bot_event` returns the scheduled task so tests can await it
directly instead of polling. `gemini.decide` is stubbed (never the real API).
"""

import asyncio

import httpx
import pytest
import respx
from sqlalchemy import select

from app.ai import gemini
from app.config import get_settings
from app.db.models import AiAction
from app.db.session import async_session_maker
from app.services import orchestrator, sync

CHATWOOT = "http://chatwoot-rails:3000"

MESSAGES_RESPONSE = {
    "payload": [
        {
            "id": 1,
            "content": "Where is my order?",
            "message_type": 0,
            "private": False,
            "created_at": 1_700_000_000,
            "sender": {"id": 55, "name": "Jane Doe", "email": "jane@example.com"},
        },
    ]
}


def _payload(conversation_id=42, status="pending", sender_type="contact", message_type="incoming"):
    return {
        "event": "message_created",
        "id": 501,
        "content": "Where is my order?",
        "message_type": message_type,
        "private": False,
        "conversation": {"id": conversation_id, "status": status},
        "sender": {"id": 55, "name": "Jane Doe", "email": "jane@example.com", "type": sender_type},
    }


@pytest.fixture(autouse=True)
def _fast_debounce(monkeypatch):
    monkeypatch.setattr(orchestrator, "DEBOUNCE_SECONDS", 0)
    orchestrator._pending_tasks.clear()
    yield
    orchestrator._pending_tasks.clear()


def _stub_decide(decision):
    async def _fake(system_prompt, context, client=None):
        return decision

    return _fake


async def _ai_action_rows(conversation_ref):
    async with async_session_maker() as session:
        result = await session.execute(
            select(AiAction).where(AiAction.conversation_ref == conversation_ref)
        )
        return result.scalars().all()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"message_type": "outgoing"},
        {"status": "open"},
        {"sender_type": "agent_bot"},
    ],
)
async def test_ineligible_events_are_ignored(monkeypatch, kwargs):
    called = {"n": 0}

    async def _fake(*args, **kwargs):
        called["n"] += 1
        return gemini.Decision("handoff_to_human", {"reason": "n/a"}, None, None)

    monkeypatch.setattr(gemini, "decide", _fake)

    task = await orchestrator.handle_bot_event(_payload(**kwargs))

    assert task is None
    assert called["n"] == 0


async def test_event_for_non_message_created_is_ignored():
    payload = _payload()
    payload["event"] = "conversation_updated"

    task = await orchestrator.handle_bot_event(payload)

    assert task is None


@respx.mock
async def test_suggest_mode_send_reply_posts_private_note_and_reopens(monkeypatch):
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages").mock(
        return_value=httpx.Response(200, json=MESSAGES_RESPONSE)
    )
    create_message = respx.post(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages"
    ).mock(return_value=httpx.Response(200, json={"id": 999}))
    toggle_status = respx.post(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/42/toggle_status"
    ).mock(return_value=httpx.Response(200, json={"success": True}))

    monkeypatch.setattr(
        gemini, "decide", _stub_decide(gemini.Decision("send_reply", {"text": "Try restarting the app."}, None, 12))
    )

    task = await orchestrator.handle_bot_event(_payload())
    assert task is not None
    await task

    assert create_message.call_count == 1
    body = create_message.calls.last.request.content
    assert b"Suggested reply" in body
    assert b"Try restarting the app." in body
    assert b'"private": true' in body or b'"private":true' in body

    assert toggle_status.call_count == 1
    assert b"open" in toggle_status.calls.last.request.content

    rows = await _ai_action_rows("chatwoot:42")
    assert len(rows) == 1
    assert rows[0].decision == "send_reply"
    assert rows[0].prompt_tokens == 12
    assert rows[0].model == get_settings().gemini_model


@respx.mock
async def test_auto_mode_send_reply_sends_public_message_via_bot_token_and_stays_pending(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "agent_mode", "auto")

    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages").mock(
        return_value=httpx.Response(200, json=MESSAGES_RESPONSE)
    )
    create_message = respx.post(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages"
    ).mock(return_value=httpx.Response(200, json={"id": 999}))
    toggle_status = respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/toggle_status")

    monkeypatch.setattr(
        gemini, "decide", _stub_decide(gemini.Decision("send_reply", {"text": "Here's your answer."}, None, 5))
    )

    task = await orchestrator.handle_bot_event(_payload())
    await task

    assert create_message.call_count == 1
    request = create_message.calls.last.request
    assert b'"private": false' in request.content or b'"private":false' in request.content
    assert b"Here's your answer." in request.content
    assert request.headers["api_access_token"] == settings.chatwoot_bot_token

    assert not toggle_status.called  # auto-sent replies stay pending


@respx.mock
async def test_suggest_mode_escalate_to_ticket_calls_sync_and_reopens(monkeypatch):
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages").mock(
        return_value=httpx.Response(200, json=MESSAGES_RESPONSE)
    )
    toggle_status = respx.post(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/42/toggle_status"
    ).mock(return_value=httpx.Response(200, json={"success": True}))

    received = {}

    async def _fake_escalate(conversation_id, reason=None, priority=None, summary=None):
        received["args"] = (conversation_id, reason, priority, summary)

    monkeypatch.setattr(sync, "escalate_conversation", _fake_escalate)
    monkeypatch.setattr(
        gemini,
        "decide",
        _stub_decide(
            gemini.Decision(
                "escalate_to_ticket",
                {"reason": "needs a refund", "priority": "high", "summary": "Refund request"},
                None,
                7,
            )
        ),
    )

    task = await orchestrator.handle_bot_event(_payload())
    await task

    assert received["args"] == (42, "needs a refund", "high", "Refund request")
    assert toggle_status.call_count == 1

    rows = await _ai_action_rows("chatwoot:42")
    assert rows[0].decision == "escalate_to_ticket"


@respx.mock
async def test_suggest_mode_handoff_to_human_reopens_without_sending_reply(monkeypatch):
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages").mock(
        return_value=httpx.Response(200, json=MESSAGES_RESPONSE)
    )
    create_message = respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages")
    toggle_status = respx.post(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/42/toggle_status"
    ).mock(return_value=httpx.Response(200, json={"success": True}))

    monkeypatch.setattr(
        gemini, "decide", _stub_decide(gemini.Decision("handoff_to_human", {"reason": "unclear ask"}, None, 3))
    )

    task = await orchestrator.handle_bot_event(_payload())
    await task

    assert not create_message.called
    assert toggle_status.call_count == 1

    rows = await _ai_action_rows("chatwoot:42")
    assert rows[0].decision == "handoff_to_human"


@respx.mock
async def test_second_event_during_processing_does_not_cancel_in_flight_work(monkeypatch):
    """Debounce cancellation must only apply while a task is still in its
    sleep phase. Once processing has started (past the sleep, mid-Gemini or
    mid-Chatwoot call), a newer message for the same conversation must NOT
    kill it midway — that would leave partial side effects (e.g. an
    ai_actions row logged but the reply never posted). The newer event
    schedules a fresh debounce instead, and both runs complete."""
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages").mock(
        return_value=httpx.Response(200, json=MESSAGES_RESPONSE)
    )
    create_message = respx.post(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages"
    ).mock(return_value=httpx.Response(200, json={"id": 999}))
    respx.post(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/42/toggle_status"
    ).mock(return_value=httpx.Response(200, json={"success": True}))

    gate = asyncio.Event()
    decide_calls = {"n": 0}

    async def gated_decide(system_prompt, context, client=None):
        decide_calls["n"] += 1
        await gate.wait()  # park mid-processing (well past the debounce sleep)
        return gemini.Decision("send_reply", {"text": f"reply {decide_calls['n']}"}, None, 1)

    monkeypatch.setattr(gemini, "decide", gated_decide)

    task1 = await orchestrator.handle_bot_event(_payload())
    assert task1 is not None
    for _ in range(1000):  # let task1 get past the sleep, into decide
        if decide_calls["n"]:
            break
        await asyncio.sleep(0)
    else:
        gate.set()
        pytest.fail("task1 never reached gemini.decide")

    # Second message arrives while task1 is mid-processing.
    task2 = await orchestrator.handle_bot_event(_payload())
    assert task2 is not None
    assert task2 is not task1

    gate.set()
    await asyncio.gather(task1, task2, return_exceptions=True)

    assert not task1.cancelled()
    assert not task2.cancelled()
    # No partial state: both runs logged their decision AND posted their note.
    assert create_message.call_count == 2
    rows = await _ai_action_rows("chatwoot:42")
    assert len(rows) == 2


@respx.mock
async def test_create_message_failure_in_suggest_mode_is_logged_not_raised(monkeypatch, caplog):
    """A transient Chatwoot 500 while executing the decision must be logged
    (logger.exception, consistent with responder/sync conventions), never
    escape the background task as 'Task exception was never retrieved'. The
    ai_actions row was already written before execution and must survive."""
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages").mock(
        return_value=httpx.Response(200, json=MESSAGES_RESPONSE)
    )
    respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages").mock(
        return_value=httpx.Response(500, json={"error": "boom"})
    )
    respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/toggle_status").mock(
        return_value=httpx.Response(200, json={"success": True})
    )

    monkeypatch.setattr(
        gemini, "decide", _stub_decide(gemini.Decision("send_reply", {"text": "Hi."}, None, 2))
    )

    task = await orchestrator.handle_bot_event(_payload())
    await task  # must not raise

    assert "failed executing decision" in caplog.text
    rows = await _ai_action_rows("chatwoot:42")
    assert len(rows) == 1
    assert rows[0].decision == "send_reply"
