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
from app.services import orchestrator

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
async def test_log_decision_persists_all_three_token_counts(monkeypatch):
    """P8 task 1 added output_tokens/cached_tokens to gemini.Decision and to
    the ai_actions schema, but nothing wired the two new fields into a written
    row. This asserts against the persisted row, not the Decision object, so
    it fails the way the original gap did: a real column left NULL forever."""
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages").mock(
        return_value=httpx.Response(200, json=MESSAGES_RESPONSE)
    )
    respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages").mock(
        return_value=httpx.Response(200, json={"id": 999})
    )
    respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/toggle_status").mock(
        return_value=httpx.Response(200, json={"success": True})
    )

    monkeypatch.setattr(
        gemini,
        "decide",
        _stub_decide(
            gemini.Decision(
                "send_reply", {"text": "Try restarting the app."}, None,
                prompt_tokens=100, output_tokens=42, cached_tokens=7,
            )
        ),
    )

    task = await orchestrator.handle_bot_event(_payload())
    await task

    rows = await _ai_action_rows("chatwoot:42")
    assert len(rows) == 1
    assert rows[0].prompt_tokens == 100
    assert rows[0].output_tokens == 42
    assert rows[0].cached_tokens == 7


@respx.mock
async def test_log_decision_persists_none_not_zero_when_usage_metadata_absent(monkeypatch):
    """A Decision with no usage metadata at all (e.g. the retry-exhausted
    handoff fallback, which never had a response object) must persist NULL
    on all three token columns -- not 0, which would misreport a call that
    was never measured as a call that cost nothing."""
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages").mock(
        return_value=httpx.Response(200, json=MESSAGES_RESPONSE)
    )
    respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/toggle_status").mock(
        return_value=httpx.Response(200, json={"success": True})
    )

    monkeypatch.setattr(
        gemini,
        "decide",
        _stub_decide(
            gemini.Decision(
                "handoff_to_human", {"reason": "n/a"}, None,
                prompt_tokens=None, output_tokens=None, cached_tokens=None,
            )
        ),
    )

    task = await orchestrator.handle_bot_event(_payload())
    await task

    rows = await _ai_action_rows("chatwoot:42")
    assert len(rows) == 1
    assert rows[0].prompt_tokens is None
    assert rows[0].output_tokens is None
    assert rows[0].cached_tokens is None


@respx.mock
async def test_log_decision_persists_a_genuine_zero_as_zero_not_none(monkeypatch):
    """A field the SDK actually reported as 0 (e.g. no cached content on this
    call) must persist as 0, staying distinguishable from 'not captured'."""
    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages").mock(
        return_value=httpx.Response(200, json=MESSAGES_RESPONSE)
    )
    respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages").mock(
        return_value=httpx.Response(200, json={"id": 999})
    )
    respx.post(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/toggle_status").mock(
        return_value=httpx.Response(200, json={"success": True})
    )

    monkeypatch.setattr(
        gemini,
        "decide",
        _stub_decide(
            gemini.Decision(
                "send_reply", {"text": "Sure."}, None,
                prompt_tokens=50, output_tokens=10, cached_tokens=0,
            )
        ),
    )

    task = await orchestrator.handle_bot_event(_payload())
    await task

    rows = await _ai_action_rows("chatwoot:42")
    assert len(rows) == 1
    assert rows[0].prompt_tokens == 50
    assert rows[0].output_tokens == 10
    assert rows[0].cached_tokens == 0
    assert rows[0].cached_tokens is not None


@respx.mock
async def test_decision_logged_before_a_failing_execution_still_records_tokens(monkeypatch, caplog):
    """Every decision is logged to ai_actions BEFORE it's executed. When
    execution then fails (a Chatwoot 500), the row logged beforehand must
    still carry the token counts the decision actually knew -- the failure
    path must not leave a partially-written audit row."""
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
        gemini,
        "decide",
        _stub_decide(
            gemini.Decision(
                "send_reply", {"text": "Hi."}, None,
                prompt_tokens=20, output_tokens=8, cached_tokens=None,
            )
        ),
    )

    task = await orchestrator.handle_bot_event(_payload())
    await task  # must not raise

    assert "failed executing decision" in caplog.text
    rows = await _ai_action_rows("chatwoot:42")
    assert len(rows) == 1
    assert rows[0].prompt_tokens == 20
    assert rows[0].output_tokens == 8
    assert rows[0].cached_tokens is None


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
async def test_auto_escalate_acks_customer_and_reopens(monkeypatch):
    """There is no external ticketing backend: `escalate_to_ticket` always
    acknowledges the customer publicly and reopens for a human, so the
    customer is never left in silence."""
    settings = get_settings()
    monkeypatch.setattr(settings, "agent_mode", "auto")
    monkeypatch.setattr(
        settings,
        "handoff_default_message",
        "Let me connect you with our team — someone will be right with you.",
    )

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
        gemini,
        "decide",
        _stub_decide(
            gemini.Decision("escalate_to_ticket", {"summary": "wants to buy S70"}, None, 7)
        ),
    )

    task = await orchestrator.handle_bot_event(_payload())
    await task  # must not raise

    assert create_message.call_count == 1
    body = create_message.calls.last.request.content
    assert b"connect you with our team" in body
    assert b'"private": false' in body or b'"private":false' in body
    assert toggle_status.call_count == 1
    assert b"open" in toggle_status.calls.last.request.content

    rows = await _ai_action_rows("chatwoot:42")
    assert rows[0].decision == "escalate_to_ticket"


@respx.mock
async def test_chatwoot_only_handoff_reopens_even_if_ack_post_fails(monkeypatch):
    """Fail-open: if posting the customer acknowledgment 500s, the conversation
    must STILL be reopened for a human — the reopen is what pulls a human in,
    and a failed ack post must never abort it (nor escape the background task)."""
    settings = get_settings()
    monkeypatch.setattr(settings, "handoff_default_message", "One moment please.")

    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages").mock(
        return_value=httpx.Response(200, json=MESSAGES_RESPONSE)
    )
    create_message = respx.post(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages"
    ).mock(return_value=httpx.Response(500, json={"error": "boom"}))
    toggle_status = respx.post(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/42/toggle_status"
    ).mock(return_value=httpx.Response(200, json={"success": True}))

    monkeypatch.setattr(
        gemini,
        "decide",
        _stub_decide(gemini.Decision("handoff_to_human", {"reason": "unclear ask"}, None, 3)),
    )

    task = await orchestrator.handle_bot_event(_payload())
    await task  # must not raise

    assert create_message.call_count == 1  # ack was attempted
    assert toggle_status.call_count == 1  # ...and the reopen still happened


@respx.mock
async def test_handoff_posts_default_message_when_no_persona_configured(monkeypatch):
    """With no assistant persona handoff_message (proton has none), the tenant
    `handoff_default_message` fallback is posted publicly so the customer is
    always acknowledged before the conversation is reopened for a human."""
    settings = get_settings()
    monkeypatch.setattr(settings, "handoff_default_message", "Connecting you to a human agent.")

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
        gemini,
        "decide",
        _stub_decide(gemini.Decision("handoff_to_human", {"reason": "n/a"}, None, 3)),
    )

    task = await orchestrator.handle_bot_event(_payload())
    await task

    assert create_message.call_count == 1
    body = create_message.calls.last.request.content
    assert b"Connecting you to a human agent." in body
    assert b'"private": false' in body or b'"private":false' in body
    assert toggle_status.call_count == 1


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
async def test_log_decision_db_failure_does_not_block_execution(monkeypatch, caplog):
    """`_log_decision`'s DB write (the ai_actions audit row) is not a
    precondition for executing the decision -- a DB blip there must be
    logged and swallowed, and the decision must still execute normally."""

    class _BoomSession:
        def add(self, obj):
            pass

        async def commit(self):
            from sqlalchemy.exc import SQLAlchemyError

            raise SQLAlchemyError("boom")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

    monkeypatch.setattr(orchestrator, "async_session_maker", lambda: _BoomSession())

    respx.get(f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages").mock(
        return_value=httpx.Response(200, json=MESSAGES_RESPONSE)
    )
    create_message = respx.post(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages"
    ).mock(return_value=httpx.Response(200, json={"id": 999}))
    respx.post(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/42/toggle_status"
    ).mock(return_value=httpx.Response(200, json={"success": True}))

    monkeypatch.setattr(
        gemini, "decide", _stub_decide(gemini.Decision("send_reply", {"text": "Hi."}, None, 2))
    )

    task = await orchestrator.handle_bot_event(_payload())
    await task  # must not raise

    assert "failed to log ai_actions row" in caplog.text
    assert create_message.call_count == 1


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


def test_build_thread_maps_roles_and_drops_noise():
    from app.services.orchestrator import _build_thread
    messages = [
        {"message_type": 0, "content": "hello", "private": False},
        {"message_type": 1, "content": "Hi! How can I help?", "private": False},
        {"message_type": 1, "content": "internal note", "private": True},   # dropped: private
        {"message_type": 2, "content": "Assigned to X", "private": False},  # dropped: activity
        {"message_type": 0, "content": "  ", "private": False},              # dropped: empty
        {"message_type": 0, "content": "tanya pasal proton x50", "private": False},
    ]
    assert _build_thread(messages) == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "Hi! How can I help?"},
        {"role": "user", "content": "tanya pasal proton x50"},
    ]


def test_build_thread_empty_when_nothing_qualifies():
    from app.services.orchestrator import _build_thread
    assert _build_thread([{"message_type": 2, "content": "act", "private": False}]) == []


async def test_lifecycle_pre_check_threads_inbox_id(monkeypatch):
    """_maybe_handle_lifecycle_reply must pass the inbox_id from the conversation
    object in the payload through to handle_lifecycle_reply so that survey_ai
    and thanks message overrides are resolvable per-operator."""
    from unittest.mock import AsyncMock
    from app.services import lifecycle, lifecycle_store

    # Set up lifecycle state so the pre-check routes through handle_lifecycle_reply.
    conv_id = 99
    inbox_id_in_payload = 7
    await lifecycle_store.transition(conv_id, lifecycle.AWAITING_SURVEY, survey_variant="ai")

    captured: dict = {}

    async def _fake_handle(conversation_id, text, state, inbox_id=None):
        captured["inbox_id"] = inbox_id

    monkeypatch.setattr(lifecycle, "handle_lifecycle_reply", _fake_handle)

    payload = {
        "event": "message_created",
        "id": 801,
        "content": "4",
        "message_type": "incoming",
        "private": False,
        "conversation": {"id": conv_id, "status": "open", "inbox_id": inbox_id_in_payload},
        "sender": {"id": 10, "name": "User", "type": "contact"},
    }

    # Enable lifecycle in settings so the pre-check runs.
    from app.config import get_settings
    monkeypatch.setattr(get_settings(), "lifecycle_enabled", True, raising=False)

    await orchestrator._maybe_handle_lifecycle_reply(payload)

    assert captured.get("inbox_id") == inbox_id_in_payload, (
        f"Expected inbox_id={inbox_id_in_payload!r} but got {captured.get('inbox_id')!r}; "
        "survey_ai/thanks overrides would silently fall back to defaults"
    )
