"""The persona-slug path rewrites a customer record, so its guards matter more
than its happy path: it must stay inert when the flag is off, when there is no
slug, and when anything downstream fails."""

from __future__ import annotations

from typing import Any

import pytest

from app.services.orchestrator import _maybe_apply_persona_slug


class FakeChatwoot:
    """Records update_contact calls; lets each method be made to fail."""

    def __init__(self, messages: Any, fail_get: bool = False, fail_update: bool = False):
        self._messages = messages
        self._fail_get = fail_get
        self._fail_update = fail_update
        self.updates: list[tuple[int, dict]] = []

    async def get_messages(self, conversation_id: int) -> Any:
        if self._fail_get:
            raise RuntimeError("chatwoot down")
        return self._messages

    async def update_contact(self, contact_id: int, payload: dict) -> Any:
        if self._fail_update:
            raise RuntimeError("update rejected")
        self.updates.append((contact_id, payload))
        return {"payload": {"id": contact_id}}


def _incoming(content: str) -> dict:
    return {"message_type": 0, "content": content}


def _outgoing(content: str) -> dict:
    return {"message_type": 1, "content": content}


async def test_applies_a_valid_slug_and_rewrites_the_contact():
    cw = FakeChatwoot({"payload": [_incoming("halo [konservatif]")]})
    assert await _maybe_apply_persona_slug(cw, 1, 4) == "konservatif"
    assert len(cw.updates) == 1
    contact_id, payload = cw.updates[0]
    assert contact_id == 4
    assert payload["name"] == "[DEMO] Sari Wijaya"
    assert payload["custom_attributes"]["risk_profile"] == "Konservatif"


async def test_no_slug_makes_no_write():
    cw = FakeChatwoot({"payload": [_incoming("berapa saldo RDN saya?")]})
    assert await _maybe_apply_persona_slug(cw, 1, 4) is None
    assert cw.updates == []


async def test_unknown_slug_makes_no_write():
    cw = FakeChatwoot({"payload": [_incoming("halo [tidakada]")]})
    assert await _maybe_apply_persona_slug(cw, 1, 4) is None
    assert cw.updates == []


async def test_only_the_newest_incoming_message_is_considered():
    # An older slug must not be re-applied on every later turn -- that would pin
    # the persona instead of switching it once.
    cw = FakeChatwoot(
        {"payload": [_incoming("halo [agresif]"), _incoming("berapa saldo saya?")]}
    )
    assert await _maybe_apply_persona_slug(cw, 1, 4) is None
    assert cw.updates == []


async def test_outgoing_messages_are_ignored():
    # The bot echoing a slug back must never switch persona.
    cw = FakeChatwoot(
        {"payload": [_incoming("halo"), _outgoing("baik [konservatif]")]}
    )
    assert await _maybe_apply_persona_slug(cw, 1, 4) is None
    assert cw.updates == []


async def test_a_later_slug_switches_again():
    cw = FakeChatwoot(
        {"payload": [_incoming("halo [agresif]"), _incoming("dan ini [moderat]")]}
    )
    assert await _maybe_apply_persona_slug(cw, 1, 4) == "moderat"
    assert cw.updates[0][1]["name"] == "[DEMO] Budi Santoso"


async def test_fetch_failure_is_swallowed():
    cw = FakeChatwoot({"payload": [_incoming("halo [moderat]")]}, fail_get=True)
    assert await _maybe_apply_persona_slug(cw, 1, 4) is None
    assert cw.updates == []


async def test_update_failure_is_swallowed():
    # A failed switch must cost the switch, not the reply -- this runs inside a
    # background task where an escaping exception is an unretrieved-exception log.
    cw = FakeChatwoot({"payload": [_incoming("halo [moderat]")]}, fail_update=True)
    assert await _maybe_apply_persona_slug(cw, 1, 4) is None


async def test_malformed_payload_is_swallowed():
    for bad in ({"payload": "not a list"}, {}, None, [], "nonsense"):
        cw = FakeChatwoot(bad)
        assert await _maybe_apply_persona_slug(cw, 1, 4) is None
        assert cw.updates == []


async def test_bare_payload_list_is_accepted():
    # Chatwoot returns {"payload": [...]} but tolerate a bare list too, the way
    # the rest of the client does.
    cw = FakeChatwoot([_incoming("halo [agresif]")])
    assert await _maybe_apply_persona_slug(cw, 1, 4) == "agresif"
