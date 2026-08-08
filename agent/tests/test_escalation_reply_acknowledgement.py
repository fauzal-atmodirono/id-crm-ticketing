"""P1 task 6 (agent side) — a linked internal reply records an acknowledgement.

A PIC or dealer answering by email IS the acknowledgement Appendix B asks for,
but until now it left no trace the SLA engine could read: no *agent* had typed
in Chatwoot, so the case still counted as un-answered and kept breaching.

The call is best-effort and default-off. It must never cost the private note
that tells the operator a reply arrived.
"""

import httpx
import respx

from app.clients.deps import get_proton_config_client
from app.config import get_settings
from app.services import escalation_replies

from tests.test_escalation_reply_linking import (
    CHATWOOT,
    PROTON,
    _enable,
    _payload,
    _stub_chatwoot,
)


def _ack_route(response: httpx.Response | None = None):
    return respx.post(f"{PROTON}/escalation/acknowledge").mock(
        return_value=response or httpx.Response(200, json={"status": "ok"})
    )


@respx.mock
async def test_a_linked_internal_reply_records_an_acknowledgement(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(get_settings(), "escalation_reply_acknowledgement_enabled", True)
    _stub_chatwoot()
    ack = _ack_route()

    await escalation_replies.maybe_link_escalation_reply(_payload())

    assert ack.called
    body = ack.calls.last.request.read().decode()
    assert '"conversation_id": "42"' in body or '"conversation_id":"42"' in body
    assert "a@test" in body
    get_proton_config_client.cache_clear()


@respx.mock
async def test_no_acknowledgement_is_recorded_when_the_flag_is_off(monkeypatch):
    _enable(monkeypatch)
    monkeypatch.setattr(get_settings(), "escalation_reply_acknowledgement_enabled", False)
    _stub_chatwoot()
    ack = _ack_route()

    await escalation_replies.maybe_link_escalation_reply(_payload())

    assert not ack.called
    get_proton_config_client.cache_clear()


@respx.mock
async def test_a_failing_acknowledgement_does_not_lose_the_private_note(monkeypatch):
    """The whole point of best-effort: a backend outage costs a metric, not
    the note telling the operator their dealer replied."""
    _enable(monkeypatch)
    monkeypatch.setattr(get_settings(), "escalation_reply_acknowledgement_enabled", True)
    messages = _stub_chatwoot()
    _ack_route(httpx.Response(500))

    await escalation_replies.maybe_link_escalation_reply(_payload())

    assert messages.called
    body = messages.calls.last.request.read().decode()
    assert "Komang" in body
    get_proton_config_client.cache_clear()


@respx.mock
async def test_a_customer_reply_records_no_acknowledgement(monkeypatch):
    """The customer answering is not the customer being acknowledged."""
    _enable(monkeypatch)
    monkeypatch.setattr(get_settings(), "escalation_reply_acknowledgement_enabled", True)
    _stub_chatwoot()
    respx.post(
        f"{CHATWOOT}/api/v1/accounts/1/conversations/42/messages"
    ).mock(return_value=httpx.Response(200, json={"id": 1}))
    ack = _ack_route()

    await escalation_replies.maybe_link_escalation_reply(
        _payload(sender="customer@test")
    )

    assert not ack.called
    get_proton_config_client.cache_clear()
