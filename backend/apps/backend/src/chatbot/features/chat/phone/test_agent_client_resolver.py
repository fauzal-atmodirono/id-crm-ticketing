"""Stage 1: ring the agent this conversation is already assigned to.

The recurring assertion is "resolves to None" -- because None here is not a
failure, it is the fall-through that hands the caller to the next resolver
and ultimately to the PSTN hunt group. A resolver that raised, or that
returned a target for an unregistered agent, would cost a live caller a ring
timeout or the whole call."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from chatbot.features.chat.phone.agent_client_resolver import (
    AgentClientResolver,
    ChainedResolver,
)
from chatbot.features.chat.phone.handoff_target import HandoffTarget


@pytest.fixture
def settings():
    from chatbot.platform.config import get_settings  # noqa: PLC0415

    return get_settings().model_copy(update={"phone_agent_softphone_enabled": True})


@pytest.fixture
def log_port():
    port = AsyncMock()
    port.get_conversation_assignee.return_value = "17"
    return port


@pytest.fixture
def registry():
    reg = AsyncMock()
    reg.registered_ids.return_value = {17}
    return reg


def _resolver(settings, log_port, registry, ticket_id="42"):
    return AgentClientResolver(settings, log_port, registry, lambda: ticket_id)


async def test_assigned_and_registered_resolves_to_a_client_target(settings, log_port, registry):
    target = await _resolver(settings, log_port, registry).resolve()
    assert target == HandoffTarget(kind="client", value="agent_17")


async def test_assigned_but_not_registered_resolves_none(settings, log_port, registry):
    """Assigned in Chatwoot but no CRM tab open. Dialling would burn the whole
    stage-1 ring on an identity Twilio cannot route to."""
    registry.registered_ids.return_value = set()
    assert await _resolver(settings, log_port, registry).resolve() is None


async def test_no_assignee_resolves_none(settings, log_port, registry):
    log_port.get_conversation_assignee.return_value = None
    assert await _resolver(settings, log_port, registry).resolve() is None


async def test_flag_off_resolves_none_without_any_lookup(settings, log_port, registry):
    off = settings.model_copy(update={"phone_agent_softphone_enabled": False})
    assert await _resolver(off, log_port, registry).resolve() is None
    log_port.get_conversation_assignee.assert_not_awaited()
    registry.registered_ids.assert_not_awaited()


async def test_missing_ticket_id_resolves_none_with_zero_port_calls(settings, log_port, registry):
    """`ticket_id` is unset when the call-start create failed or the tenant
    runs chatwoot_enabled=False. This path runs INLINE in the audio pump, so
    the win is making zero round trips, not just returning None."""
    resolver = AgentClientResolver(settings, log_port, registry, lambda: None)
    assert await resolver.resolve() is None
    log_port.get_conversation_assignee.assert_not_awaited()
    registry.registered_ids.assert_not_awaited()


async def test_port_failure_resolves_none_and_does_not_raise(settings, log_port, registry):
    log_port.get_conversation_assignee.side_effect = RuntimeError("chatwoot down")
    assert await _resolver(settings, log_port, registry).resolve() is None


async def test_non_numeric_assignee_resolves_none(settings, log_port, registry):
    log_port.get_conversation_assignee.return_value = "not-an-id"
    assert await _resolver(settings, log_port, registry).resolve() is None


async def test_prefetch_makes_resolve_do_no_io(settings, log_port, registry):
    """Same reasoning as HandoffTargetResolver.prefetch(): _attempt_transfer
    runs inline in pump(), so a round trip there is dead air the caller
    actually hears."""
    resolver = _resolver(settings, log_port, registry)
    await resolver.prefetch()
    log_port.get_conversation_assignee.reset_mock()
    registry.registered_ids.reset_mock()

    assert await resolver.resolve() == HandoffTarget(kind="client", value="agent_17")
    log_port.get_conversation_assignee.assert_not_awaited()
    registry.registered_ids.assert_not_awaited()


async def test_prefetch_failure_leaves_resolve_working(settings, log_port, registry):
    resolver = _resolver(settings, log_port, registry)
    log_port.get_conversation_assignee.side_effect = RuntimeError("boom")
    await resolver.prefetch()
    log_port.get_conversation_assignee.side_effect = None
    assert await resolver.resolve() == HandoffTarget(kind="client", value="agent_17")


async def test_prefetch_does_not_cache_when_the_ticket_does_not_exist_yet(
    settings, log_port, registry
):
    """CRITICAL fix (whole-branch review): "no ticket yet" is not an answer,
    it is "not knowable yet" -- caching it as a permanent None would mean
    stage 1 can never resolve a <Client> target for the rest of the call,
    no matter that the ticket is created a moment later by the concurrently
    running ticket-create task. See test_bridge.py's end-to-end regression
    test for the full production shape this unit test isolates."""
    resolver = AgentClientResolver(settings, log_port, registry, lambda: None)

    await resolver.prefetch()

    assert resolver._warm is False
    log_port.get_conversation_assignee.assert_not_awaited()
    registry.registered_ids.assert_not_awaited()


async def test_prefetch_after_the_ticket_appears_resolves_live_not_stuck_cold(
    settings, log_port, registry
):
    """Once the ticket id becomes available, prefetch() (fired again, or
    resolve()'s own inline fallback) must actually find the assignee --
    proving the earlier no-ticket prefetch left nothing behind to get in
    the way."""
    ticket_id_holder = {"value": None}
    resolver = AgentClientResolver(settings, log_port, registry, lambda: ticket_id_holder["value"])

    await resolver.prefetch()  # too early -- must not cache
    ticket_id_holder["value"] = "42"

    assert await resolver.resolve() == HandoffTarget(kind="client", value="agent_17")


async def test_chain_returns_the_first_non_none():
    first, second = AsyncMock(), AsyncMock()
    first.resolve.return_value = None
    second.resolve.return_value = HandoffTarget(kind="pstn", value="+60388889999")

    assert await ChainedResolver([first, second]).resolve() == HandoffTarget(
        kind="pstn", value="+60388889999"
    )


async def test_chain_stops_at_the_first_hit():
    first, second = AsyncMock(), AsyncMock()
    first.resolve.return_value = HandoffTarget(kind="client", value="agent_17")

    await ChainedResolver([first, second]).resolve()
    second.resolve.assert_not_awaited()


async def test_chain_survives_a_raising_resolver():
    """One broken resolver must not deny the caller the fallback behind it."""
    first, second = AsyncMock(), AsyncMock()
    first.resolve.side_effect = RuntimeError("boom")
    second.resolve.return_value = HandoffTarget(kind="pstn", value="+60388889999")

    assert await ChainedResolver([first, second]).resolve() is not None


async def test_chain_survives_a_slow_resolver_and_still_tries_the_fallback():
    """Whole-branch review fix (Important 8): a resolver that is SLOW but
    never actually fails must not consume the whole outer wait_for budget
    `bridge._attempt_transfer` wraps the chain in -- the fallback resolver
    behind it must still get tried, same as when the slow resolver raises
    outright."""
    import asyncio  # noqa: PLC0415

    first, second = AsyncMock(), AsyncMock()

    async def _hangs() -> HandoffTarget | None:
        await asyncio.sleep(10)
        return None  # pragma: no cover -- never reached, cancelled first

    first.resolve.side_effect = _hangs
    second.resolve.return_value = HandoffTarget(kind="pstn", value="+60388889999")

    result = await asyncio.wait_for(ChainedResolver([first, second]).resolve(), timeout=5.0)

    assert result == HandoffTarget(kind="pstn", value="+60388889999")
    second.resolve.assert_awaited_once()
