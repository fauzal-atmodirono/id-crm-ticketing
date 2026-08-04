"""The bot-safety invariant's three layers (see client.py's module
docstring): seeded conversations must never end up `pending` on a
bot-enabled inbox, because a `pending` conversation with an incoming
customer message is exactly what `agent/app/services/orchestrator.py` acts
on -- a 100-contact seed run would otherwise be ~140 Gemini calls and AI
replies fired against a live client tenant.

This package deliberately has no HTTP mock harness (see client.py's module
docstring), so layers 2 and 3 -- both real network code -- are split into a
thin I/O wrapper plus a pure decision function, and it is the pure decision
functions that are exhaustively tested here. Layer 1 (`_safe_status`) is
already pure.
"""

from __future__ import annotations

from client import (
    _safe_status,
    created_conversation_status_refusal_reason,
    inbox_seeding_refusal_reason,
)

# --- layer 1: _safe_status ---------------------------------------------------


def test_safe_status_passes_through_open_and_resolved_unchanged():
    assert _safe_status("open") == "open"
    assert _safe_status("resolved") == "resolved"


def test_safe_status_remaps_pending_to_open():
    # The one status this module must never ask Chatwoot to create.
    assert _safe_status("pending") == "open"


def test_safe_status_remaps_any_unrecognised_status_to_open():
    # Fail closed on anything the generator might produce that isn't in the
    # known-safe set, not just the specific "pending" case.
    assert _safe_status("snoozed") == "open"
    assert _safe_status("") == "open"


# --- layer 2: inbox_seeding_refusal_reason -----------------------------------

_API_INBOX = {"id": 5, "channel_type": "Channel::Api"}
_EMAIL_INBOX = {"id": 5, "channel_type": "Channel::Email"}
_NO_BOT = {"agent_bot": {}}
_BOT_ATTACHED = {"agent_bot": {"id": 3, "name": "Proton Assistant"}}


def test_api_inbox_with_no_bot_is_safe():
    assert inbox_seeding_refusal_reason(5, _API_INBOX, _NO_BOT) is None


def test_refuses_when_an_agent_bot_is_attached():
    reason = inbox_seeding_refusal_reason(5, _API_INBOX, _BOT_ATTACHED)
    assert reason is not None
    assert "agent bot" in reason
    assert "3" in reason


def test_refuses_a_non_api_channel_type():
    reason = inbox_seeding_refusal_reason(5, _EMAIL_INBOX, _NO_BOT)
    assert reason is not None
    assert "Channel::Api" in reason
    assert "Channel::Email" in reason


def test_refuses_when_both_the_channel_and_the_bot_are_wrong():
    # The channel check must win when both are broken -- it's checked first,
    # matching the order the I/O wrapper makes its two GETs in.
    reason = inbox_seeding_refusal_reason(5, _EMAIL_INBOX, _BOT_ATTACHED)
    assert reason is not None
    assert "Channel::Api" in reason


def test_a_payload_missing_the_agent_bot_field_entirely_is_treated_as_no_bot():
    # A malformed/unexpected agent_bot response must not crash the check --
    # and must not be silently treated as "bot attached" either, since that
    # isn't what it says.
    assert inbox_seeding_refusal_reason(5, _API_INBOX, {}) is None


def test_a_null_agent_bot_response_is_treated_as_no_bot():
    assert inbox_seeding_refusal_reason(5, _API_INBOX, {"agent_bot": None}) is None


def test_an_agent_bot_dict_with_no_id_is_treated_as_no_bot():
    # Mirrors the real jbuilder shape for "no bot attached": {"agent_bot": {}}.
    assert inbox_seeding_refusal_reason(5, _API_INBOX, {"agent_bot": {}}) is None


def test_missing_channel_type_is_refused_not_assumed_safe():
    assert inbox_seeding_refusal_reason(5, {"id": 5}, _NO_BOT) is not None


# --- layer 3: created_conversation_status_refusal_reason ---------------------


def test_open_created_status_is_accepted():
    assert created_conversation_status_refusal_reason(42, "open", "open") is None


def test_resolved_created_status_is_accepted():
    assert created_conversation_status_refusal_reason(42, "resolved", "resolved") is None


def test_refuses_a_pending_created_status():
    # The exact failure this layer exists to catch: Chatwoot's
    # before_create :determine_conversation_status silently overrode the
    # requested status because the inbox has an active bot.
    reason = created_conversation_status_refusal_reason(42, "open", "pending")
    assert reason is not None
    assert "pending" in reason
    assert "42" in reason


def test_refuses_a_response_with_no_status_key_at_all():
    # Fail closed: if this Chatwoot doesn't render status on create, the
    # readback can't run, so it must not be assumed safe.
    reason = created_conversation_status_refusal_reason(42, "open", None)
    assert reason is not None
    assert "no 'status' field" in reason
